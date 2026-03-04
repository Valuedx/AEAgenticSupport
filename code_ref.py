"""
Agentic Flow v3 — Master Agent + Diagnostic Agent + Remediation Agent
+ SOP-Based AE Recovery Flow + Schedule Activity Monitoring + KB Integration
===================================================================
CHANGES IN v3:
  1. SOP steps run silently in the background — no technical step messages shown to user
  2. User sees only friendly, engaging status updates ("I'm looking into this...",
     "Your agent is back online!", "Workflow has been restarted!" etc.)
  3. Final response is a clean human-readable summary, not a raw internal dump
  4. State management unchanged — aistudio_conv_state stores and restores correctly
     (NOT pulling from a DB — it uses the conv/dialog param store as before)
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
import requests
import asyncio
import subprocess
# Ensure current directory is in sys.path for local imports (like db_context)
dir_path = os.path.dirname(os.path.abspath(__file__))
if dir_path not in sys.path:
    sys.path.insert(0, dir_path)
from urllib.parse import quote
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# ── AI Studio / Bot Framework ─────────────────────────────────────────────
try:
    from botbuilder.core import TurnContext
    from botbuilder.schema import Activity, ActivityTypes
    from aistudiobot.aistudio.dialog.state import (
        AIStudioConvState,
        AIStudioUserState,
    )
except ImportError:
    class TurnContext: pass
    class Activity: pass
    class ActivityTypes: 
        typing = "typing"
    class AIStudioConvState: pass
    class AIStudioUserState: pass

# ── Vertex AI ─────────────────────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    raise ImportError("Run: pip install google-genai")

# ── PostgreSQL / pgvector ─────────────────────────────────────────────────
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    raise ImportError("Run: pip install psycopg2-binary")

# ── Logging ───────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/agentic.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── DB Persistence Layer (v3.8) ──────────────────────────────────────────
try:
    from db_context import save_message, upsert_user_context, fetch_user_history
except ImportError:
    logger.warning("db_context.py not found in path. Persistence will be limited to UI state.")
    def save_message(*args, **kwargs): pass
    def upsert_user_context(*args, **kwargs): pass
    def fetch_user_history(*args, **kwargs): return []


# ===========================================================================
# ── Multi-Agent System ────────────────────────────────────────────────────
try:
    from multi_agent import (
        run_multi_agent_flow,
        AgentMessage,
        InfoRequest,
    )
    MULTI_AGENT_ENABLED = True
    logger.info("MultiAgent: multi_agent.py loaded successfully.")
except ImportError:
    MULTI_AGENT_ENABLED = False
    logger.warning("MultiAgent: multi_agent.py not found — multi-agent features disabled.")



# ===========================================================================
# ─── CONFIGURATION ──────────────────────────────────────────────────────────
# ===========================================================================

VERTEX_PROJECT            = os.getenv("VERTEX_PROJECT", "")
VERTEX_LOCATION           = os.getenv("VERTEX_LOCATION", "us-central1")
VERTEX_MODEL              = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
EMBED_MODEL               = os.getenv("EMBED_MODEL", "text-embedding-005")
VERTEX_EMBEDDING_LOCATION = os.getenv("VERTEX_EMBEDDING_LOCATION") or VERTEX_LOCATION
SA_KEY_PATH               = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("SERVICE_ACCOUNT_KEY_PATH", "")

PG_HOST     = os.getenv("PG_HOST", "localhost")
PG_PORT     = int(os.getenv("PG_PORT", "5432"))
PG_DB       = os.getenv("PG_DB", "rpa_workflows_db")
PG_USER     = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "root")
PG_TABLE    = os.getenv("PG_TABLE", "rpa_workflows")

T4_BASE_URL = os.getenv("T4_BASE_URL", "https://t4.automationedge.com/aeengine/rest")
T4_USERNAME = os.getenv("T4_USERNAME", "")
T4_PASSWORD = os.getenv("T4_PASSWORD", "")
T4_ORG_CODE = os.getenv("T4_ORG_CODE", "")

# ServiceNow
SNOW_BASE_URL         = os.getenv("SNOW_BASE_URL", "")
SNOW_USERNAME         = os.getenv("SNOW_USERNAME", "")
SNOW_PASSWORD         = os.getenv("SNOW_PASSWORD", "")
SNOW_ASSIGNMENT_GROUP = os.getenv("SNOW_ASSIGNMENT_GROUP", "IT Support")

# Knowledge Base (AIStudio KM)
KB_BASE_URL    = os.getenv("KB_BASE_URL", "")
KB_PROJECT_ID  = os.getenv("KB_PROJECT_ID", "")
KB_SECRET      = os.getenv("KB_PROJECT_SECRET", "")
KB_ENABLED     = os.getenv("KB_ENABLED", "true").lower() == "true"

# v3.8: LLM Kill-Switch — set to false to run in rule-only/fallback mode
LLM_ENABLED    = os.getenv("LLM_ENABLED", "true").lower() == "true"

# v3.5/v3.8: Recovery Mode — set to 'light' to skip RCA and Ticket creation for faster testing
RECOVERY_MODE  = os.getenv("RECOVERY_MODE", "full").lower()

# SOP Recovery thresholds
MAX_AGENT_RESTART_ATTEMPTS    = 3
MAX_WORKFLOW_RESTART_ATTEMPTS = 3
AGENT_RESTART_WAIT_SEC        = 45
WORKFLOW_RESTART_WAIT_SEC     = 30
SCHEDULE_DELAY_THRESHOLD_MIN  = 30

POLL_INTERVAL_SEC  = 3
NO_AGENT_THRESHOLD = 10
MAX_POLL_ATTEMPTS  = 100

_t4_session_token: str = ""
_vertex_client: Optional[genai.Client] = None
_embed_client:  Optional[genai.Client] = None


# ===========================================================================
# ─── STREAMING CALLBACK ──────────────────────────────────────────────────────
# ===========================================================================

# Two-level callback system:
#   - emit_user()  → sends a friendly message visible to the user (via stream callback)
#   - log_internal() → writes to logs only, never shown to user
MessageCallback = Callable[[str], Awaitable[None]]


async def _noop_stream(msg: str) -> None:
    """Default no-op stream — used when no callback provided."""
    pass  # Intentionally silent; logger handles internal details


# ===========================================================================
# ─── LLM GUARDRAIL (v3.7) ──────────────────────────────────────────────────
# ===========================================================================

def safe_llm_call(fn: Callable, fallback: Any, *args, **kwargs) -> Any:
    """Wraps an LLM call with a fallback to ensure system resilience."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.warning(f"LLM Call Guardrail: Using fallback due to error: {e}")
        return fallback


def get_vertex_client(location: Optional[str] = None) -> genai.Client:
    global _vertex_client, _embed_client
    target_location = location or VERTEX_LOCATION

    if target_location == VERTEX_LOCATION and _vertex_client is not None:
        return _vertex_client
    if target_location == VERTEX_EMBEDDING_LOCATION and _embed_client is not None:
        return _embed_client

    if SA_KEY_PATH and os.path.exists(SA_KEY_PATH):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SA_KEY_PATH

    if not VERTEX_PROJECT:
        raise ValueError("VERTEX_PROJECT env var is required.")

    client = genai.Client(vertexai=True, project=VERTEX_PROJECT, location=target_location)
    logger.info(f"Vertex AI: Client ready — project={VERTEX_PROJECT}, location={target_location}")

    if target_location == VERTEX_LOCATION:
        _vertex_client = client
    elif target_location == VERTEX_EMBEDDING_LOCATION:
        _embed_client = client
    return client


# ===========================================================================
# ─── VERTEX AI: LLM CALL ────────────────────────────────────────────────────
# ===========================================================================

def vertex_generate(
    prompt: str,
    system_prompt: Optional[str] = None,
    response_format: str = "text",
    temperature: float = 0.0,
    max_output_tokens: int = 2048,
) -> str:
    client = get_vertex_client()
    contents = [genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])]
    system_instruction = None
    if system_prompt:
        system_instruction = genai_types.Content(role="system", parts=[genai_types.Part(text=system_prompt)])

    config = genai_types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json" if response_format == "json" else "text/plain"
    )

    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=VERTEX_MODEL, contents=contents, config=config)
            text = _extract_vertex_text(resp).strip()
            return text
        except Exception as e:
            if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                raise
    return ""


def _extract_vertex_text(resp: Any) -> str:
    buf: List[str] = []
    for candidate in getattr(resp, "candidates", []):
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []):
            t = getattr(part, "text", None)
            if t:
                buf.append(str(t))
    return "".join(buf)


def repair_truncated_json(json_str: str) -> str:
    """
    Attempts to repair a truncated JSON string by closing dangling quotes,
    brackets, and braces. Useful for LLM responses that cut off.
    """
    json_str = json_str.strip()
    if not json_str:
        return "{}"
        
    stack = []
    is_in_string = False
    escaped = False
    
    repaired_chars = []
    
    for char in json_str:
        if is_in_string:
            if char == '"' and not escaped:
                is_in_string = False
            elif char == '\\' and not escaped:
                escaped = True
            else:
                escaped = False
        else:
            if char == '"':
                is_in_string = True
            elif char == '{' or char == '[':
                stack.append(char)
            elif char == '}':
                if stack and stack[-1] == '{':
                    stack.pop()
            elif char == ']':
                if stack and stack[-1] == '[':
                    stack.pop()
        repaired_chars.append(char)
        
    # Repair logic
    if is_in_string:
        # If we were in a string, close the quote
        repaired_chars.append('"')
        
    # Close any open structures in reverse order
    while stack:
        # Before closing, check for trailing commas and whitespace
        while repaired_chars and repaired_chars[-1].isspace():
            repaired_chars.pop()
        if repaired_chars and repaired_chars[-1] == ',':
            repaired_chars.pop()
            
        opener = stack.pop()
        if opener == '{':
            repaired_chars.append('}')
        elif opener == '[':
            repaired_chars.append(']')
            
    return "".join(repaired_chars)

def _clean_json(text: str) -> str:
    """Strip markdown code blocks, extraneous text, and trailing commas."""
    import re
    text = text.strip()
    
    # 1. Remove markdown code blocks if present
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
    
    # 2. Extract the first {...} block if there's still surrounding text
    if not text.startswith("{"):
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
            
    # 3. Handle trailing commas before closing braces/brackets
    text = re.sub(r",\s*(\})", r"\1", text)
    text = re.sub(r",\s*(\])", r"\1", text)
    
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        # 4. Repair truncation (close dangling quotes/braces)
        text = repair_truncated_json(text)
        # Final cleanup for punctuation
        text = re.sub(r",\s*(\})", r"\1", text)
        text = re.sub(r",\s*(\])", r"\1", text)
        return text


def embed_text(text: str) -> List[float]:
    """Step 3: Generate Embedding"""
    client = get_vertex_client(location=VERTEX_EMBEDDING_LOCATION)
    try:
        res = client.models.embed_content(
            model=EMBED_MODEL,
            contents=[text],
            config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
        )
        return res.embeddings[0].values
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        # Return dummy vector if failed to avoid crash
        return [0.0] * 768



# ===========================================================================
# ─── KNOWLEDGE BASE (AIStudio KM) INTEGRATION ───────────────────────────────
# ===========================================================================

def kb_search(
    query: str,
    top_k: int = 3,
    kb_project_id: Optional[str] = None,
    kb_secret: Optional[str] = None,
) -> List[Dict]:
    if not KB_ENABLED:
        return []

    proj_id = kb_project_id or KB_PROJECT_ID
    secret  = kb_secret or KB_SECRET
    base    = KB_BASE_URL

    if not proj_id or not secret or not base:
        logger.warning("KB: KB_BASE_URL, KB_PROJECT_ID or KB_PROJECT_SECRET not configured.")
        return []

    try:
        resp = requests.post(
            f"{base}/api/v1/km/search",
            headers={
                "Content-Type": "application/json",
                "X-Project-Id": proj_id,
                "X-Project-Secret": secret,
            },
            json={"query": query, "topK": top_k},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()

        articles = []
        items = (
            results.get("results") or results.get("articles") or
            results.get("data") or results.get("hits") or []
        )
        for item in items:
            articles.append({
                "title":   item.get("title") or item.get("name") or "",
                "content": item.get("content") or item.get("text") or item.get("body") or "",
                "score":   float(item.get("score") or item.get("relevance") or 0),
                "id":      str(item.get("id") or item.get("article_id") or ""),
            })
        logger.info(f"KB: Found {len(articles)} articles for query: '{query[:60]}'")
        return articles

    except Exception as e:
        logger.warning(f"KB: Search failed: {e}")
        return []


def kb_build_context(articles: List[Dict], max_chars: int = 2000) -> str:
    if not articles:
        return ""

    parts = ["=== KNOWLEDGE BASE CONTEXT ==="]
    total = 0
    for art in articles:
        snippet = art["content"][:600]
        entry = f"\n[KB: {art['title']}]\n{snippet}"
        if total + len(entry) > max_chars:
            break
        parts.append(entry)
        total += len(entry)

    return "\n".join(parts)


# ===========================================================================
# ─── SYSTEM PROMPTS ─────────────────────────────────────────────────────────
# ===========================================================================

MASTER_SYSTEM_PROMPT = """You are the Master Agent of an intelligent RPA Orchestration system.
You decide how to handle user queries and route them to the correct agent.
Always be friendly, professional, and reassuring in your responses."""

RESPONDER_SYSTEM_PROMPT = """You are a warm, helpful RPA automation assistant named AISBot.
You answer user questions conversationally — like a knowledgeable colleague, not a manual.
Use simple language, avoid jargon, and add light encouragement where appropriate.
Use emojis sparingly to make responses feel approachable (e.g. ✅ 🤖 💡).
Always reply in plain text. Never output JSON or markdown code blocks."""

DIAGNOSTIC_SYSTEM_PROMPT = """You are the Diagnostic Operations Agent.
Your role is READ-ONLY: check status, monitor systems, fetch reports, view data.
You CANNOT make any changes or execute any actions that modify a system.
Reply with valid JSON when asked for structured data."""

REMEDIATION_SYSTEM_PROMPT = """You are the Remediation Operations Agent.
Your role is ACTION: fix issues, execute workflows, process files, restart services.
You CAN make changes to systems.
Be friendly and reassuring — tell the user what you're doing and why.
Reply with valid JSON when asked for structured data."""

ANALYSIS_SYSTEM_PROMPT = """You are an intelligent RPA Orchestrator.
Your goal is to analyze a user query against provided SOPs (Standard Operating Procedures) and candidate Workflows.
You must select the best workflow, provide a confidence score, and reference the relevant SOP.

Return ONLY a valid JSON object following this schema:
{
  "issue_summary": "Short explanation of the user's issue",
  "diagnostic_type": "Diagnostic Remediation",
  "selected_workflow": "Name of the chosen workflow",
  "workflow_id": "ID of the chosen workflow",
  "confidence_score": 0.95,
  "matched_sop_reference": "Reference or Title of the SOP",
  "suggested_category": "ServiceNow category (e.g., Software, Hardware, HR)",
  "suggested_assignment_group": "Target support group (e.g., IT Support, Payroll Team)",
  "sop_analysis_steps": [
    "Analyzed query context against matching SOPs...",
    "Identified required workflow parameters...",
    "Validated confidence score for automation..."
  ]
}"""


# ===========================================================================
# ─── HELPER: HUMAN-FRIENDLY WORKFLOW NAME ────────────────────────────────────
# ===========================================================================

def humanize_workflow_name(name: str) -> str:
    """
    Convert internal workflow names into friendly human-readable titles.
    Examples:
        WF_add_employee       → Add Employee
        WFF_generate_payslip  → Generate Payslip
        WF-leave-request      → Leave Request
    """
    import re
    # Strip common technical prefixes (case-insensitive)
    cleaned = re.sub(r'^(?:WF[_\-]F?[_\-]?|WFF[_\-])', '', name, flags=re.IGNORECASE)
    # Replace underscores and hyphens with spaces
    cleaned = cleaned.replace('_', ' ').replace('-', ' ')
    # Title-case each word
    return cleaned.strip().title()


# ===========================================================================
# ─── MASTER AGENT FUNCTIONS ─────────────────────────────────────────────────
# ===========================================================================

def master_agent_route(
    user_query: str,
    workflow_catalogue: Optional[str] = None,
    sop_catalogue: Optional[str] = None,
) -> str:
    q = user_query.lower()

    # --- HARD RULES (v3.7: Deterministic Overrides) ---
    if any(x in q for x in ["agent status", "check agent", "agent down", "agent up", "restart agent", "reboot agent"]):
        logger.info(f"Master Agent: Hard-coded override -> intent='agent_status'")
        return "agent_status"

    if any(x in q for x in ["fix", "recover", "not running", "failed", "issue", "error", "troubleshoot", "investigate"]):
        logger.info(f"Master Agent: Hard-coded override -> intent='recovery'")
        return "recovery"

    if any(x in q for x in ["run ", "execute ", "process ", "trigger "]):
        logger.info(f"Master Agent: Hard-coded override -> intent='remediation'")
        return "remediation"

    if any(x in q for x in ["check ", "status ", "monitor ", "fetch ", "view "]):
        logger.info(f"Master Agent: Hard-coded override -> intent='diagnostic'")
        return "diagnostic"

    if any(x in q for x in ["show ", "list ", "available ", "workflows", "all tasks"]):
        logger.info(f"Master Agent: Hard-coded override -> intent='informational'")
        return "informational"

    # --- FALLBACK TO LLM (only if LLM_ENABLED) ---
    if not LLM_ENABLED:
        logger.info("Master Agent: LLM_ENABLED=false — defaulting to 'natural'.")
        return "natural"

    # Build context section if knowledge is provided
    context_section = ""
    if workflow_catalogue or sop_catalogue:
        context_lines = ["\n=== SYSTEM KNOWLEDGE (use this to classify accurately) ==="]
        if sop_catalogue:
            context_lines.append(f"\nAvailable SOPs (Standard Operating Procedures):\n{sop_catalogue}")
        if workflow_catalogue:
            context_lines.append(f"\nAvailable Automation Workflows:\n{workflow_catalogue}")
        context_lines.append("\nIf the user query relates to any of the above workflows or SOPs, classify as 'remediation' or 'diagnostic' accordingly.")
        context_section = "\n".join(context_lines)

    prompt = f"""Classify this user query into exactly one category:

1. "natural"       — Greetings, small talk, general conversation.
2. "agent_status"  — Anything about checking, monitoring, or restarting the T4 automation AGENT itself.
3. "diagnostic"    — Asking to check, view, monitor, or report on WORKFLOW status or logs.
4. "remediation"   — Any command or request to trigger/run/execute/start a workflow.
5. "informational" — General requests to see a list of ALL available workflows or capabilities.
                     Examples: "Show all workflows", "What can you do?", "List available tasks"

IMPORTANT:
- If the user wants to see a list of workflows → "informational"
- If the query mentions "agent" health/status → "agent_status"
- If the query is a short action command → "remediation"
{context_section}

User query: "{user_query}"

Reply with ONE word only: natural, agent_status, diagnostic, remediation, or informational."""

    result = vertex_generate(prompt, system_prompt=MASTER_SYSTEM_PROMPT)
    intent = result.strip().lower().split()[0]

    if intent not in ("natural", "agent_status", "diagnostic", "remediation"):
        logger.warning(f"Master Agent: Unknown intent '{intent}', defaulting to 'natural'")
        intent = "natural"

    logger.info(f"Master Agent: '{user_query[:60]}' -> intent='{intent}'")
    return intent





def master_agent_respond(user_query: str, kb_context: str = "", history: str = "") -> str:
    kb_section = f"\n\n{kb_context}\n" if kb_context else ""
    history_section = f"\n\nRecent Conversation History:\n{history}\n" if history else ""
    
    prompt = f"""You are a helpful RPA automation assistant.{kb_section}{history_section}
Answer the following question clearly and concisely.

User: "{user_query}"
"""
    response = vertex_generate(prompt, system_prompt=RESPONDER_SYSTEM_PROMPT)
    if response.startswith("{") and "message" in response:
        try:
            data = json.loads(response)
            if "message" in data:
                response = data["message"]
        except Exception:
            pass
    return response


def master_agent_summarize(user_query: str, intent: str) -> str:
    prompt = f"""Summarize this user query in one concise sentence.
Capture: the operation type, key entities (system names, file types, modules).

User query: "{user_query}"
Intent type: {intent}

Write the summary only — no punctuation at end, no explanation."""

    summary = vertex_generate(prompt, system_prompt=MASTER_SYSTEM_PROMPT).strip()
    logger.info(f"Master Agent: Summary = '{summary}'")
    return summary


# ===========================================================================
# ─── T4 REST API FUNCTIONS ──────────────────────────────────────────────────
# ===========================================================================

def t4_authenticate(username: Optional[str] = None, password: Optional[str] = None) -> str:
    global _t4_session_token
    u = username or T4_USERNAME
    p = password or T4_PASSWORD

    if not u or not p:
        logger.error("T4: T4_USERNAME or T4_PASSWORD not set.")
        return ""

    try:
        resp = requests.post(
            f"{T4_BASE_URL}/authenticate",
            params={"username": u, "password": p},
            timeout=30,
        )
        resp.raise_for_status()
        _t4_session_token = resp.json().get("sessionToken", "")
        if _t4_session_token:
            logger.info(f"T4: Authenticated. Token: {_t4_session_token[:15]}...")
    except Exception as e:
        logger.error(f"T4: Auth failed: {e}")
        _t4_session_token = ""

    return _t4_session_token


def _get_token(session_token: Optional[str] = None) -> str:
    global _t4_session_token
    token = session_token or _t4_session_token
    if not token:
        if T4_USERNAME and T4_PASSWORD:
            token = t4_authenticate()
    if not token:
        raise RuntimeError("Not authenticated. Set T4_USERNAME and T4_PASSWORD in .env")
    return token


def t4_check_agent_status(org_code: Optional[str] = None, session_token: Optional[str] = None) -> List[Dict]:
    token = _get_token(session_token)
    org = org_code or T4_ORG_CODE

    if not org:
        logger.error("T4: T4_ORG_CODE not configured — cannot check agents.")
        return []

    for attempt in range(3):
        try:
            resp = requests.get(
                f"{T4_BASE_URL}/{org}/monitoring/agents",
                params={"type": "AGENT", "offset": 0, "size": 100},
                headers={"Content-Type": "application/json", "X-Session-Token": token},
                timeout=30,
            )
            resp.raise_for_status()
            agents = resp.json()

            if isinstance(agents, dict):
                agents = agents.get("data") or agents.get("agents") or [agents]

            state = agents[0].get("agentState", "UNKNOWN") if agents else "NO_AGENTS"
            logger.info(f"T4: {len(agents)} agent(s). First state: {state}")
            return agents
        except (requests.ConnectionError, requests.Timeout) as e:
            logger.warning(f"T4: Agent check attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(2)
            else:
                raise
    return []


def t4_get_agent_monitoring(
    org_code: Optional[str] = None,
    session_token: Optional[str] = None,
    offset: int = 0,
    size: int = 10
) -> List[Dict]:
    """
    Separate tool for T4 Agent Monitoring using POST as per user spec.
    Checks if an agent is running or not and returns monitoring details.
    """
    token = _get_token(session_token)
    org = org_code or T4_ORG_CODE
    
    url = f"{T4_BASE_URL}/{org}/monitoring/agents"
    params = {"type": "AGENT", "offset": offset, "size": size}
    
    try:
        logger.info(f"T4 Monitoring: Fetching agent status from {url}...")
        resp = requests.post(
            url,
            params=params,
            headers={"Content-Type": "application/json", "X-Session-Token": token},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        
        # Consistent data extraction logic
        agents = []
        if isinstance(result, dict):
            agents = result.get("data") or result.get("agents") or [result]
        elif isinstance(result, list):
            agents = result
            
        logger.info(f"T4 Monitoring: Found {len(agents)} agents.")
        return agents
    except Exception as e:
        logger.error(f"T4 Monitoring failed: {e}")
        return []


def t4_get_agent_id_from_inspect(
    agent_name: str,
    org_code: Optional[str] = None,
    session_token: Optional[str] = None,
) -> Optional[str]:
    token = _get_token(session_token)
    org = org_code or T4_ORG_CODE

    try:
        # T4 usually supports name or ID in path, but ID is more robust.
        # If agent_name looks like an ID (numeric or UUID), we use it directly.
        # Otherwise, we use the encoded name.
        encoded_id = quote(agent_name)
        resp = requests.get(
            f"{T4_BASE_URL}/{org}/agents/{encoded_id}/inspect",
            headers={"Content-Type": "application/json", "X-Session-Token": token},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        agent_id = (
            data.get("agentId") or data.get("id") or
            data.get("agent_id") or data.get("ID") or
            str(data.get("agentDetails", {}).get("agentId", ""))
        )
        if agent_id:
            logger.info(f"T4: Inspect resolved agent '{agent_name}' -> ID={agent_id}")
            return str(agent_id)
    except Exception as e:
        logger.warning(f"T4: Inspect failed for '{agent_name}': {e} — falling back to monitoring list ID")

    return None


def t4_get_agent_details(
    org_code: Optional[str] = None,
    session_token: Optional[str] = None,
    target_agent_name: Optional[str] = None,
) -> Dict:
    agents = t4_check_agent_status(org_code=org_code, session_token=session_token)
    if not agents:
        return {}

    # 🔹 Smart Selection: 
    # 1. If target_agent_name provided, try to find it
    # 2. Otherwise, find the first 'CONNECTED' or 'RUNNING' agent
    # 3. Fallback to agents[0]
    
    selected_agent = None
    if target_agent_name:
        selected_agent = next((a for a in agents if a.get("agentName") == target_agent_name), None)
    
    if not selected_agent:
        # v3.8 enhancement: Prefer RUNNING/CONNECTED agents
        selected_agent = next((a for a in agents if a.get("agentState", "").upper() in ("CONNECTED", "RUNNING")), agents[0])

    agent = selected_agent
    agent_name  = agent.get("agentName", "Unknown")
    fallback_id = str(agent.get("agentId") or agent.get("id") or "Unknown")
    agent_state = agent.get("agentState", "UNKNOWN").upper()

    inspect_id = t4_get_agent_id_from_inspect(
        agent_name=agent_name,
        org_code=org_code,
        session_token=session_token,
    )
    resolved_id = inspect_id or fallback_id

    return {
        "agentName":  agent_name,
        "agentId":    resolved_id,
        "agentState": agent_state,
        "raw":        agent,
    }



def t4_detect_agent_bin_path() -> Optional[str]:
    """
    Auto-detects the AutomationEdge Agent 'bin' directory.
    Heuristics:
    1. Check if 'aeagent.exe' is currently running and get its executable path.
    2. Search common drive roots (C:, D:) for 'ae-agent/bin/startup.bat'.
    3. Check environment variables like AE_AGENT_HOME.
    """
    logger.info("T4: Attempting to auto-detect agent bin path...")

    # Heuristic 1: Running Process
    try:
        cmd = 'Powershell -Command "Get-Process aeagent -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path"'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            exe_path = res.stdout.strip().split('\n')[0] # Get first match
            if os.path.isfile(exe_path):
                bin_dir = os.path.dirname(exe_path)
                logger.info(f"T4: Detected agent bin path from running process: {bin_dir}")
                return bin_dir
    except Exception as e:
        logger.debug(f"T4: Process detection heuristic failed: {e}")

    # Heuristic 2: Common Drive Search (Shallow)
    # We look for ae-agent/bin or AutomationEdgeAgent/bin
    search_dirs = [
        "C:\\ae-agent\\bin",
        "D:\\ae-agent\\bin",
        "C:\\AutomationEdgeAgent\\bin",
        "D:\\AutomationEdgeAgent\\bin",
    ]
    for d in search_dirs:
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "startup.bat")):
            logger.info(f"T4: Detected agent bin path via common directory search: {d}")
            return d

    # Heuristic 3: Environment Variable
    env_home = os.getenv("AE_AGENT_HOME")
    if env_home:
        bin_path = os.path.join(env_home, "bin")
        if os.path.isdir(bin_path):
            logger.info(f"T4: Detected agent bin path via AE_AGENT_HOME: {bin_path}")
            return bin_path

    logger.warning("T4: Could not auto-detect agent bin path.")
    return None


# t4_local_restart_agent_service was removed in favor of manual guidance.


def t4_check_workflow_status(
    workflow_name: str,
    request_id: Optional[str] = None,
    org_code: Optional[str] = None,
    session_token: Optional[str] = None,
) -> Dict:
    token = _get_token(session_token)
    org = org_code or T4_ORG_CODE

    if request_id:
        url = f"{T4_BASE_URL}/workflowinstances/{request_id}"
    else:
        url = f"{T4_BASE_URL}/{org}/workflows/{workflow_name}/instances"

    resp = requests.get(
        url,
        headers={"Content-Type": "application/json", "X-Session-Token": token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data:
        data = data[0]
    logger.info(f"T4: Workflow status for '{workflow_name}': {data.get('status', 'Unknown')}")
    return data


def t4_check_log_files(
    workflow_name: str,
    request_id: Optional[str] = None,
    org_code: Optional[str] = None,
    session_token: Optional[str] = None,
) -> Dict:
    token = _get_token(session_token)
    org = org_code or T4_ORG_CODE

    if request_id:
        url = f"{T4_BASE_URL}/workflowinstances/{request_id}/logs"
    else:
        url = f"{T4_BASE_URL}/{org}/workflows/{workflow_name}/logs"

    try:
        resp = requests.get(
            url,
            headers={"Content-Type": "application/json", "X-Session-Token": token},
            timeout=30,
        )
        resp.raise_for_status()
        logs = resp.json()
        logger.info(f"T4: Logs retrieved for '{workflow_name}'.")
        return {"success": True, "logs": logs}
    except Exception as e:
        logger.warning(f"T4: Log check failed: {e}")
        return {"success": False, "error": str(e), "logs": []}


def t4_restart_workflow(
    workflow_name: str,
    workflow_id: str,
    collected_params: Dict[str, str],
    workflow_params_schema: List[Dict],
    org_code: Optional[str] = None,
    session_token: Optional[str] = None,
) -> str:
    token = _get_token(session_token)
    org = org_code or T4_ORG_CODE

    params_list = [
        {
            "name": p["name"],
            "value": collected_params.get(p["name"], ""),
            "type": p.get("type", "String"),
        }
        for p in workflow_params_schema
    ]

    payload = {
        "orgCode": org,
        "workflowName": workflow_name,
        "source": "Python Agentic Client — Restart",
        "responseMailSubject": "null",
        "params": params_list,
    }

    resp = requests.post(
        f"{T4_BASE_URL}/execute",
        headers={"Content-Type": "application/json", "X-Session-Token": token},
        params={"workflow_name": workflow_name, "workflow_id": workflow_id},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()

    if not body.get("success", True):
        error_msg = body.get("errorDetails") or "Unknown T4 error"
        raise RuntimeError(f"T4 Workflow Restart Error: {error_msg}")

    request_id = body.get("automationRequestId") or body.get("requestId") or body.get("id")
    if not request_id:
        raise RuntimeError("T4 Workflow Restart Error: No request ID in response.")

    logger.info(f"T4: Workflow restarted. New request_id={request_id}")
    return str(request_id)


def t4_execute_workflow(
    workflow_name: str,
    workflow_id: str,
    params: List[Dict[str, str]],
    org_code: Optional[str] = None,
    user_id: Optional[str] = None,
    mail_subject: Optional[str] = None,
    session_token: Optional[str] = None,
) -> str:
    token = _get_token(session_token)
    org = org_code or T4_ORG_CODE

    payload = {
        "orgCode": org,
        "workflowName": workflow_name,
        "source": "Python Agentic Client",
        "responseMailSubject": mail_subject or "null",
        "params": params,
    }

    resp = requests.post(
        f"{T4_BASE_URL}/execute",
        headers={"Content-Type": "application/json", "X-Session-Token": token},
        params={"workflow_name": workflow_name, "workflow_id": workflow_id},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()

    if not body.get("success", True):
        raise RuntimeError(f"T4 Execution Error: {body.get('errorDetails', 'Unknown')}")

    request_id = body.get("automationRequestId") or body.get("requestId") or body.get("id")
    if request_id is None:
        raise RuntimeError("T4 Execution Error: Automation Request ID not found in response.")

    logger.info(f"T4: Triggered. request_id={request_id}")
    return str(request_id)


def t4_poll_status(
    request_id: str,
    output_file_name: Optional[str] = None,
    org_code: Optional[str] = None,
    session_token: Optional[str] = None,
) -> Dict:
    token = _get_token(session_token)
    org = org_code or T4_ORG_CODE
    status = "pending"
    file_id = None
    row_count = None
    raw = None
    counter = 0

    for attempt in range(MAX_POLL_ATTEMPTS):
        urls = [
            f"{T4_BASE_URL}/workflowinstances/{request_id}",
            f"{T4_BASE_URL}/{org}/workflowinstances/{request_id}",
        ]
        resp = None
        last_error = None

        for url in urls:
            try:
                r = requests.get(
                    url,
                    headers={"Content-Type": "application/json", "X-Session-Token": token},
                    timeout=30,
                )
                if r.status_code == 200:
                    resp = r
                    break
                else:
                    last_error = f"HTTP {r.status_code}"
            except Exception as e:
                last_error = str(e)

        if not resp:
            if attempt > 5:
                raise RuntimeError(f"T4: Polling failed: {last_error}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        raw = resp.json()
        status = raw.get("status", "pending")
        logger.info(f"T4: Poll #{attempt+1} - status={status}")

        if raw.get("workflowResponse"):
            try:
                wf_resp = json.loads(raw["workflowResponse"])
                out_params = wf_resp.get("outputParameters") or []
                for op in out_params:
                    op_name = op.get("name", "")
                    if output_file_name and op_name == output_file_name and op.get("value"):
                        file_id = op["value"]
                        break
                    elif not output_file_name and op_name.endswith(".xlsx") and op.get("value"):
                        file_id = op["value"]
                        break
                if file_id is None and out_params:
                    file_id = out_params[0].get("value")
                if len(out_params) > 1:
                    row_count = out_params[1].get("value") or out_params[0].get("value")
                elif out_params:
                    row_count = out_params[0].get("value")
            except Exception as e:
                logger.warning(f"T4: workflowResponse parse error: {e}")

        if status == "New" and not raw.get("agentName"):
            counter += 1
            if counter >= NO_AGENT_THRESHOLD:
                status = "no_agent"
        else:
            counter = 0

        if status in ("Complete", "Failure", "no_agent"):
            break

        time.sleep(POLL_INTERVAL_SEC)

    return {"status": status, "request_id": request_id, "file_id": file_id, "row_count": row_count, "raw": raw}


def t4_download_file(file_id: str, request_id: str, save_path: str, session_token: Optional[str] = None) -> str:
    token = _get_token(session_token)
    resp = requests.get(
        f"{T4_BASE_URL}/file/download",
        headers={"X-Session-Token": token},
        params={"file_id": file_id, "request_id": request_id},
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    with open(save_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)
    return save_path


def t4_upload_file(file_path: str, workflow_name: str, workflow_id: str, session_token: Optional[str] = None) -> str:
    token = _get_token(session_token)
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as fh:
        resp = requests.post(
            f"{T4_BASE_URL}/file/upload",
            headers={"X-Session-Token": token},
            params={"workflow_name": workflow_name, "workflow_id": workflow_id},
            files={"file": (filename, fh)},
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json().get("fileId")


def t4_upload_bytes(file_bytes: bytes, filename: str, workflow_name: str, workflow_id: str, session_token: Optional[str] = None) -> str:
    token = _get_token(session_token)
    resp = requests.post(
        f"{T4_BASE_URL}/file/upload",
        headers={"X-Session-Token": token},
        params={"workflow_name": workflow_name, "workflow_id": workflow_id},
        files={"file": (filename, file_bytes)},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("fileId")


# ===========================================================================
# ─── FULL WORKFLOW LIST ───────────────────────────────────────────────────────
# ===========================================================================

def t4_fetch_all_workflows(session_token: Optional[str] = None) -> List[Dict]:
    token = _get_token(session_token)
    all_workflows: List[Dict] = []
    offset, page_size = 0, 200

    logger.info("T4: Fetching all workflows live from /workflows/runtime ...")
    while True:
        resp = requests.get(
            f"{T4_BASE_URL}/workflows/runtime",
            params={"offset": offset, "size": page_size},
            headers={"Content-Type": "application/json", "X-Session-Token": token},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json()

        if isinstance(page, list):
            batch = page
        elif isinstance(page, dict):
            batch = page.get("data") or page.get("workflows") or page.get("content") or []
        else:
            batch = []

        if not batch:
            break

        for wf in batch:
            wf_id   = str(wf.get("id") or wf.get("workflowId") or wf.get("workflow_id") or "")
            wf_name = wf.get("workflowName") or wf.get("name") or wf.get("workflow_name") or ""
            desc    = wf.get("description") or wf.get("desc") or ""

            params_raw = wf.get("params") or wf.get("parameters") or wf.get("inputParameters") or []
            if isinstance(params_raw, str):
                try:
                    params_raw = json.loads(params_raw)
                except Exception:
                    params_raw = []

            params = [
                {
                    "name":        p.get("name") or "",
                    "type":        p.get("type") or "String",
                    "displayName": p.get("displayName") or p.get("name") or "",
                    "optional":    p.get("optional", False),
                }
                for p in params_raw if isinstance(p, dict)
            ]

            all_workflows.append({
                "workflow_id":         wf_id,
                "workflow_name":       wf_name,
                "description":         desc,
                "params":              params,
                "assigned_agent_id":   str(wf.get("agentId") or wf.get("agent_id") or ""),
                "assigned_agent_name": wf.get("agentName") or wf.get("agent_name") or "",
                "intent_type":         "both",
            })

        logger.info(f"T4: Workflows batch offset={offset} -> {len(batch)} records")
        if len(batch) < page_size:
            break
        offset += page_size

    logger.info(f"T4: Total workflows fetched live: {len(all_workflows)}")
    return all_workflows


def t4_search_workflows(
    user_query: str,
    session_token: Optional[str] = None,
    top_k: int = 5,
    intent: Optional[str] = None,
) -> List[Dict]:
    all_wfs = t4_fetch_all_workflows(session_token=session_token)
    if not all_wfs:
        return []

    stop_words = {"run", "the", "a", "an", "is", "for", "please", "can", "you", "check", "status", "of"}
    keywords = [
        w.lower().strip() for w in user_query.split()
        if len(w) > 2 and w.lower().strip() not in stop_words
    ]

    scored_wfs = []
    for wf in all_wfs:
        score = 0
        name_lower = wf["workflow_name"].lower()
        desc_lower = wf["description"].lower()
        for kw in keywords:
            if kw in name_lower: score += 10
            if kw in desc_lower: score += 2
        if score > 0 or not keywords:
            scored_wfs.append((score, wf))

    scored_wfs.sort(key=lambda x: x[0], reverse=True)
    sample = [item[1] for item in scored_wfs[:300]]
    if not sample:
        sample = all_wfs[:300]

    catalogue_lines = []
    for i, wf in enumerate(sample):
        param_names = [p["name"] for p in wf.get("params", [])]
        catalogue_lines.append(
            f"{i}: name={wf['workflow_name']} | desc={wf['description'][:100]} | params={param_names}"
        )

    intent_hint = ""
    if intent == "diagnostic":
        intent_hint = "\nPrefer workflows that CHECK, STATUS, MONITOR, or FETCH."
    elif intent in ("remediation", "recovery"):
        intent_hint = "\nPrefer workflows that FIX, PROCESS, EXECUTE, RECONCILE, or RESTART."

    prompt = f"""Select the {top_k} most relevant T4 automation workflows for the query.{intent_hint}

User query: "{user_query}"

Workflows:
{chr(10).join(catalogue_lines)}

Reply with valid JSON only: [<index1>, <index2>, ...]"""

    try:
        raw = vertex_generate(prompt, system_prompt=MASTER_SYSTEM_PROMPT, response_format="json")
        indexes = json.loads(raw)
        if not isinstance(indexes, list):
            indexes = list(indexes.values()) if isinstance(indexes, dict) else []
    except Exception as e:
        logger.warning(f"T4 search fallback: {e}")
        indexes = list(range(min(top_k, len(sample))))

    results = []
    seen_ids = set()
    for idx in indexes:
        try:
            idx = int(idx)
            if 0 <= idx < len(sample):
                wf = sample[idx]
                if wf["workflow_id"] not in seen_ids:
                    results.append(wf)
                    seen_ids.add(wf["workflow_id"])
        except Exception:
            pass

    logger.info(f"T4 search: Selected {len(results)} workflows live.")
    return results[:top_k]


# ===========================================================================
# ─── SCHEDULE ACTIVITY MONITORING ───────────────────────────────────────────
# ===========================================================================

def check_agent_schedule_activity(
    workflow_id: str,
    workflow_name: str,
    org_code: Optional[str] = None,
    session_token: Optional[str] = None,
) -> Dict:
    token = _get_token(session_token)

    url = f"{T4_BASE_URL}/workflows/{workflow_id}/schedule"
    try:
        resp = requests.get(
            url,
            headers={"Content-Type": "application/json", "X-Session-Token": token},
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.warning(f"Schedule check failed for {workflow_name}: {e}")
        return {
            "workflow_id":       workflow_id,
            "workflow_name":     workflow_name,
            "adherence_status":  "UNKNOWN",
            "error":             str(e),
            "raw":               {},
            "schedule_type":     "UNKNOWN",
            "schedule_expr":     "",
            "schedule_enabled":  True,
            "last_scheduled":    "N/A",
            "last_actual":       None,
            "last_status":       "N/A",
            "delay_minutes":     0.0,
            "next_run":          "N/A",
            "on_time_pct":       0.0,
            "avg_delay_minutes": 0.0,
            "missed_runs_7d":    0,
        }

    schedule_config = raw.get("schedule_config", {})
    last_exec       = raw.get("last_execution", {})
    adherence       = raw.get("schedule_adherence", {})

    delay_min  = float(last_exec.get("delay_minutes", 0))
    raw_status = adherence.get("status", "").upper()

    if not raw_status:
        if delay_min <= 2:
            raw_status = "ON_TIME"
        elif delay_min <= SCHEDULE_DELAY_THRESHOLD_MIN:
            raw_status = "DELAYED"
        else:
            raw_status = "MISSED"

    if not schedule_config.get("enabled", True):
        raw_status = "DISABLED"

    result = {
        "workflow_id":       workflow_id,
        "workflow_name":     workflow_name,
        "schedule_type":     schedule_config.get("type", "UNKNOWN"),
        "schedule_expr":     schedule_config.get("expression", ""),
        "schedule_enabled":  schedule_config.get("enabled", True),
        "last_scheduled":    last_exec.get("scheduled_time", "N/A"),
        "last_actual":       last_exec.get("actual_start_time"),
        "last_status":       last_exec.get("status", "N/A"),
        "delay_minutes":     delay_min,
        "next_run":          raw.get("next_scheduled_run", "N/A"),
        "adherence_status":  raw_status,
        "on_time_pct":       float(adherence.get("on_time_percentage", 0)),
        "avg_delay_minutes": float(adherence.get("avg_delay_minutes", 0)),
        "missed_runs_7d":    int(adherence.get("missed_runs_last_7_days", 0)),
        "raw":               raw,
    }

    logger.info(f"Schedule check for '{workflow_name}': status={raw_status}, delay={delay_min} min")
    return result

# ===========================================================================
# ─── NEW RAG & VECTOR SEARCH LOGIC ───────────────────────────────────────
# ===========================================================================

def get_db_conn():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD
    )

def search_sops_vector(query_embedding: List[float], top_k: int = 3, threshold: float = 0.5) -> List[Dict]:
    """Step 4: SOP RAG Search"""
    try:
        conn = get_db_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Using cosine similarity (1 - (<=>))
            query = f"""
                SELECT title, content, reference_id, (1 - (embedding <=> %s::vector)) as similarity
                FROM sops
                ORDER BY similarity DESC
                LIMIT %s
            """
            cur.execute(query, (query_embedding, top_k))
            results = cur.fetchall()
            # Apply threshold
            if results and results[0]['similarity'] < threshold:
                logger.info(f"SOP search result below threshold ({results[0]['similarity']} < {threshold})")
                return []
            return results
    except Exception as e:
        logger.error(f"SOP Vector Search failed: {e}")
        return []

def search_workflows_vector(query_embedding: List[float], top_k: int = 5) -> List[Dict]:
    """Step 5: Workflow RAG Search"""
    try:
        conn = get_db_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = f"""
                SELECT workflow_id, workflow_name, description, params, (1 - (embedding <=> %s::vector)) as similarity
                FROM {PG_TABLE}
                ORDER BY similarity DESC
                LIMIT %s
            """
            cur.execute(query, (query_embedding, top_k))
            return cur.fetchall()
    except Exception as e:
        logger.error(f"Workflow Vector Search failed: {e}")
        return []

def log_interaction(user_query: str, intent: str, response: str, structured_data: Optional[Dict] = None, confidence: float = 0.0, workflow_id: Optional[str] = None):
    """Logging Interaction"""
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            query = """
                INSERT INTO interactions (user_query, intent, response, structured_data, confidence_score, workflow_triggered)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            cur.execute(query, (user_query, intent, response, json.dumps(structured_data) if structured_data else None, confidence, workflow_id))
        conn.commit()
    except Exception as e:
        logger.error(f"Interaction Logging failed: {e}")



# ===========================================================================
# ─── SERVICENOW TICKET ──────────────────────────────────────────────────────
# ===========================================================================

def create_servicenow_ticket(
    workflow_name: str,
    summary: str,
    description: str,
    priority: str = "3",
    category: str = "Software",
    assignment_group: str = SNOW_ASSIGNMENT_GROUP,
    username: Optional[str] = None,
) -> Dict:
    if not SNOW_BASE_URL or not SNOW_USERNAME or not SNOW_PASSWORD:
        logger.warning("ServiceNow: Credentials not configured — using mock ticket.")
        mock_num = f"INC_MOCK_{int(time.time()) % 100000:05d}"
        return {
            "success": True,
            "ticket_number": mock_num,
            "sys_id": "mock_sys_id",
            "url": f"{SNOW_BASE_URL or 'https://servicenow.example.com'}/incident/{mock_num}",
            "mock": True,
        }

    caller = username or T4_USERNAME or "automation_agent"

    payload = {
        "short_description": f"[AE Bot] {summary}",
        "description": description,
        "category": category,
        "priority": priority,
        "caller_id": caller,
        "cmdb_ci": workflow_name,
        "assignment_group": assignment_group,
    }

    try:
        resp = requests.post(
            f"{SNOW_BASE_URL}/api/now/table/incident",
            auth=(SNOW_USERNAME, SNOW_PASSWORD),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=30,
        )
        body_text = resp.text # Capture raw text for debugging
        resp.raise_for_status()
        
        try:
            result = resp.json().get("result", {})
            ticket_number = result.get("number", "INCUNKNOWN")
            sys_id = result.get("sys_id", "")
            url = f"{SNOW_BASE_URL}/incident/{ticket_number}"
            logger.info(f"ServiceNow: Ticket created — {ticket_number}")
            return {"success": True, "ticket_number": ticket_number, "sys_id": sys_id, "url": url}
        except Exception as json_err:
            logger.error(f"ServiceNow: JSON parse failed. Body: {body_text[:1000]}")
            return {"success": False, "error": f"JSON Parse error: {str(json_err)}", "ticket_number": None}

    except Exception as e:
        logger.error(f"ServiceNow: Ticket creation failed: {e}")
        return {"success": False, "error": str(e), "ticket_number": None}


# ===========================================================================
# ─── HELPER: EXTRACT WORKFLOW NAME FROM QUERY ────────────────────────────────
# ===========================================================================

def extract_workflow_name_from_query(user_query: str, known_workflows: Optional[List[Dict]] = None) -> Optional[str]:
    known_list = ""
    if known_workflows:
        known_list = "\nKnown workflow names:\n" + "\n".join(
            f"- {wf['workflow_name']}" for wf in known_workflows[:20]
        )

    prompt = f"""Extract the workflow name from this user query.
The workflow name is typically a technical identifier like "Gemo_Disk_Space_cleanup",
"GSTR_2B_1", "TDS_Tally_Recon", etc.
{known_list}

User query: "{user_query}"

Reply with ONLY the workflow name, or "UNKNOWN" if no specific workflow is mentioned.
Return "UNKNOWN" if the user is only asking about the agent status or health.
Do not include any explanation."""

    result = vertex_generate(prompt, system_prompt=MASTER_SYSTEM_PROMPT).strip()
    if result.upper() == "UNKNOWN" or not result:
        return None
    return result


# ===========================================================================
# ─── RCA ENGINE ─────────────────────────────────────────────────────────────
# ===========================================================================

RCA_CATEGORIES = {
    "agent_failure":        "The AE agent stopped or became unreachable, preventing workflow execution.",
    "schedule_miss":        "The scheduler failed to trigger the workflow at the expected time.",
    "resource_exhaustion":  "Disk space, memory, or CPU limits caused failure.",
    "data_validation":      "Input data was missing, malformed, or failed validation inside the workflow.",
    "network_connectivity": "A network timeout or connectivity issue prevented the workflow from reaching an external system.",
    "dependency_failure":   "A downstream service, database, or API that the workflow depends on was unavailable.",
    "configuration_error":  "The workflow or agent is misconfigured (wrong credentials, missing params, bad file path).",
    "concurrency_conflict": "Another instance of the same workflow was already running, causing a lock or collision.",
    "permission_error":     "The agent or workflow lacked required permissions to access a file, DB, or system.",
    "unknown":              "The root cause could not be determined. Manual investigation required.",
}

RCA_SYSTEM_PROMPT = """You are an expert RPA Root Cause Analysis engine with deep knowledge of
AutomationEdge (AE), cron schedulers, Windows/Linux agents, and workflow execution patterns.

IMPORTANT ANALYSIS RULES:
- If agent was RUNNING and workflow status is Unknown/Failed → likely configuration_error or data_validation
- If agent was STOPPED or UNKNOWN → agent_failure is primary cause
- If schedule shows MISSED and agent was running → schedule_miss or configuration_error
- If log retrieval failed with 400/404 → configuration_error (wrong endpoint or missing log path)
- If log retrieval failed with 500 → dependency_failure (log server issue)
- NEVER default to configuration_error just because some data was unavailable
- Base your conclusion on the STRONGEST evidence signal

Always reply with valid JSON only. No markdown, no explanation outside the JSON."""


def perform_rca(
    workflow_name: str,
    agent_name: Optional[str],
    agent_id: Optional[str],
    agent_status_initial: Optional[str],
    agent_status_after_restart: Optional[str],
    agent_restarted: bool,
    workflow_status: Optional[str],
    workflow_restarted: bool,
    workflow_status_after_restart: Optional[str],
    schedule_data: Optional[Dict],
    log_error_detail: Optional[str],
    log_retrieval_failed: bool,
    log_error_code: Optional[str],
    restart_attempts_exhausted: bool,
    user_query: str,
) -> Dict:
    schedule_info = "Not checked"
    if schedule_data:
        sched_status = schedule_data.get("adherence_status", "UNKNOWN")
        if sched_status != "UNKNOWN":
            schedule_info = (
                f"Schedule type: {schedule_data.get('schedule_type', 'N/A')}, "
                f"Expression: {schedule_data.get('schedule_expr', 'N/A')}, "
                f"Adherence: {sched_status}, "
                f"Delay: {schedule_data.get('delay_minutes', 0)} min, "
                f"Missed runs (7d): {schedule_data.get('missed_runs_7d', 0)}"
            )
        else:
            schedule_info = "Schedule API unavailable — not a factor in analysis"

    evidence = {
        "workflow_name":                 workflow_name,
        "user_reported_issue":           user_query,
        "agent_name":                    agent_name or "Unknown",
        "agent_id":                      agent_id or "Unknown",
        "agent_status_at_detection":     agent_status_initial or "Unknown",
        "agent_restarted":               agent_restarted,
        "agent_status_after_restart":    agent_status_after_restart or (
            "RUNNING" if agent_restarted else agent_status_initial
        ),
        "workflow_status_at_detection":  workflow_status or "Unknown",
        "workflow_restarted":            workflow_restarted,
        "workflow_status_after_restart": workflow_status_after_restart or (
            "N/A" if not workflow_restarted else "Unknown"
        ),
        "restart_attempts_exhausted":    restart_attempts_exhausted,
        "schedule_info":                 schedule_info,
        "log_retrieval_failed":          log_retrieval_failed,
        "log_http_error_code":           log_error_code or "N/A",
        "log_error_detail":              log_error_detail or "No log errors found",
    }

    categories_text = "\n".join(
        f'  "{k}": "{v}"' for k, v in RCA_CATEGORIES.items()
    )

    prompt = f"""Perform Root Cause Analysis for this AE workflow incident.

=== EVIDENCE ===
{json.dumps(evidence, indent=2)}

=== ROOT CAUSE CATEGORIES ===
{categories_text}

=== DECISION LOGIC ===
1. Agent STOPPED/UNKNOWN + workflow failed → primary=agent_failure (HIGH confidence)
2. Agent RUNNING + workflow Unknown + log 400 error → primary=configuration_error
3. Agent RUNNING + workflow Failed + log shows exception → primary=data_validation or dependency_failure
4. Schedule MISSED + agent was RUNNING → primary=schedule_miss
5. Unknown workflow status + agent RUNNING + no logs → primary=unknown (MEDIUM confidence)

Reply with this exact JSON (no markdown):
{{
  "primary_cause":        "<category key>",
  "primary_cause_label":  "<human readable label>",
  "primary_cause_detail": "<specific to THIS incident, 2-3 sentences>",
  "contributing_factors": ["<factor 1>", "<factor 2>"],
  "confidence":           "<High|Medium|Low>",
  "evidence_used":        ["<point 1>", "<point 2>", "<point 3>"],
  "recommended_fix":      "<concrete next action, 1-2 sentences>",
  "preventive_measures":  ["<prevention 1>", "<prevention 2>", "<prevention 3>"],
  "severity":             "<Critical|High|Medium|Low>",
  "estimated_impact":     "<who/what was affected and for how long>",
  "rca_summary":          "<2-3 sentence plain English summary>"
}}"""

    try:
        raw = vertex_generate(
            prompt,
            system_prompt=RCA_SYSTEM_PROMPT,
            response_format="json",
            temperature=0.1,
            max_output_tokens=1500,
        )
        rca = json.loads(raw)
        logger.info(f"RCA: cause={rca.get('primary_cause')}, confidence={rca.get('confidence')}")
        return rca
    except Exception as e:
        logger.error(f"RCA engine failed: {e}")
        return {
            "primary_cause":        "unknown",
            "primary_cause_label":  "Unknown",
            "primary_cause_detail": f"RCA engine encountered an error: {e}",
            "contributing_factors": [],
            "confidence":           "Low",
            "evidence_used":        [],
            "recommended_fix":      "Manual investigation required.",
            "preventive_measures":  ["Set up proactive monitoring alerts for agent health"],
            "severity":             "Medium",
            "estimated_impact":     "Impact scope undetermined.",
            "rca_summary":          "Root cause analysis could not be completed. Manual review required.",
        }


# ===========================================================================
# ─── USER-FACING MESSAGE BUILDERS (v3: friendly, no internal SOP details) ───
# ===========================================================================

def _user_msg_start(workflow_name: str) -> str:
    if workflow_name == "Agent Health Check":
        return "🔍 Checking the current status of your automation agent..."
    return (
        f"Got it! I'm looking into the **{workflow_name}** issue right now. "
        f"Give me a moment while I run a full health check in the background. ⏳"
    )


def _user_msg_clarify() -> str:
    return (
        "I wasn't able to identify which workflow you're referring to. "
        "Could you share the workflow name so I can investigate? "
        "(e.g. *Gemo_Disk_Space_cleanup*, *TDS_Tally_Recon*, etc.)"
    )


def _user_msg_agent_checking() -> str:
    return "🔍 Checking your automation agent's health..."


def _user_msg_agent_ok(agent_name: str) -> str:
    return f"✅ Your agent **{agent_name}** is up and running. Moving on to the workflow check..."


def _user_msg_agent_down(agent_name: str, state: str) -> str:
    return (
        f"⚠️ Heads up — your agent **{agent_name}** appears to be **{state}**. "
        f"This is likely causing the issue. I'm attempting to restart it now..."
    )


def _user_msg_agent_restarting(attempt: int, max_attempts: int) -> str:
    return f"🔄 Restarting agent... (attempt {attempt}/{max_attempts})"


def _user_msg_agent_restart_ok() -> str:
    return "✅ Great news! The agent is back online and running normally."


def _user_msg_agent_restart_failed() -> str:
    return (
        "❌ I wasn't able to restart the agent after multiple attempts. "
        "I'm escalating this now and raising a support ticket for your team."
    )


def _user_msg_workflow_checking(workflow_name: str) -> str:
    return f"🔍 Checking the status of **{workflow_name}**..."


def _user_msg_workflow_status(workflow_name: str, status: str) -> str:
    messages = {
        "Running":           f"🏃 **{workflow_name}** is currently running — looks good!",
        "New":               f"🆕 **{workflow_name}** has just been queued and should kick off shortly.",
        "Execution Started": f"▶️ **{workflow_name}** has started executing.",
        "Complete":          f"✅ **{workflow_name}** completed successfully!",
        "Failure":           f"❌ **{workflow_name}** has a failure on record. I'll try to restart it...",
        "Diverted":          f"↩️ **{workflow_name}** was diverted. Investigating further...",
        "Unknown":           f"🔵 I couldn't find a recent run for **{workflow_name}**. I'll try triggering it now...",
    }
    return messages.get(status, f"🔵 **{workflow_name}** status: {status}. Investigating...")


def _user_msg_workflow_triggering(workflow_name: str) -> str:
    return f"▶️ Triggering **{workflow_name}** now and monitoring for a result..."


def _user_msg_workflow_trigger_ok(workflow_name: str) -> str:
    return f"✅ **{workflow_name}** has been successfully triggered and is now running!"


def _user_msg_workflow_restarting(attempt: int, max_attempts: int) -> str:
    return f"🔄 Restarting the workflow... (attempt {attempt}/{max_attempts})"


def _user_msg_workflow_restart_ok(workflow_name: str) -> str:
    return f"✅ **{workflow_name}** has been restarted successfully and is running again!"


def _user_msg_workflow_restart_failed(workflow_name: str) -> str:
    return (
        f"❌ The workflow **{workflow_name}** couldn't be restarted automatically. "
        f"I'm running a root cause analysis and will raise a ticket."
    )


def _user_msg_schedule_checking() -> str:
    return "📅 Checking the workflow's schedule and recent run history..."


def _user_msg_schedule_result(sched: Dict) -> str:
    s = sched.get("adherence_status", "UNKNOWN")
    name = sched.get("workflow_name", "")
    if s == "ON_TIME":
        return f"📅 Schedule looks healthy — **{name}** has been running on time."
    elif s == "DELAYED":
        return f"⚠️ Schedule shows some delays for **{name}**. Continuing investigation..."
    elif s == "MISSED":
        return f"🚨 A scheduled run of **{name}** appears to have been missed. This may be part of the issue."
    elif s == "DISABLED":
        return f"⏸️ The schedule for **{name}** is currently **disabled**. You may want to re-enable it."
    else:
        return "📅 Schedule info is unavailable at the moment. Continuing with other checks..."


def _user_msg_logs_checking() -> str:
    return "📋 Reviewing logs to understand what went wrong..."


def _user_msg_logs_result(has_errors: bool) -> str:
    if has_errors:
        return "⚠️ Found some errors in the logs. Feeding this into the root cause analysis..."
    return "📋 Logs reviewed — no critical errors flagged."


def _user_msg_rca_running() -> str:
    return "🔬 Running root cause analysis across all the evidence collected..."


def _user_msg_ticket_creating() -> str:
    return "🎫 Creating a ServiceNow incident ticket for your team..."


def _user_msg_kb_searching() -> str:
    return "📚 Searching the knowledge base for related articles and solutions..."


def _user_msg_running_ok(workflow_name: str) -> str:
    return (
        f"✅ Good news — **{workflow_name}** is already running normally. "
        f"No action was needed!"
    )


def _build_final_summary(
    workflow_name: str,
    summary: Dict,
    rca: Dict,
    ticket: Dict,
    kb_articles: List[Dict],
    restarted_successfully: bool,
    wf_status: str,
) -> str:
    """
    Builds a clean, user-friendly final summary of what happened.
    No internal step numbers, no raw API field names.
    """
    agent_name   = summary.get("agent_name", "Unknown")
    agent_status = summary.get("agent_status", "Unknown")
    agent_action = "Restarted and back online ✅" if summary.get("agent_restarted") else f"Status: {agent_status}"

    sched_status = summary.get("schedule_status") or "Not checked"
    ticket_num   = ticket.get("ticket_number", "N/A")
    ticket_url   = ticket.get("url", "N/A")

    severity_emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}.get(
        rca.get("severity", "Medium"), "⚪"
    )
    confidence_emoji = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}.get(
        rca.get("confidence", "Low"), "⚪"
    )

    # Outcome line
    if restarted_successfully:
        outcome_title = "✅ **Resolution Confirmed**"
        outcome_body  = "The system detected an issue and has successfully restored the workflow to a healthy state."
    elif wf_status == "Running":
        outcome_title = "✅ **System Healthy**"
        outcome_body  = "A full health check was performed and all components are currently operating within normal parameters."
    else:
        outcome_title = "❌ **Attention Required**"
        outcome_body  = "Automatic recovery was not possible for this specific issue. An escalation ticket has been raised for the support team."

    # RCA detail
    rca_cause  = rca.get("primary_cause_label", "Unknown")
    rca_fix    = rca.get("recommended_fix", "Manual investigation required.")
    rca_sum    = rca.get("rca_summary", "Detailed analysis is available in the logs.")

    # KB articles
    kb_section = ""
    if kb_articles:
        kb_section = "\n\n📚 **Suggested Resources:**\n"
        for art in kb_articles:
            kb_section += f"  • {art['title']}\n"

    # Ticket Line
    ticket_info = f"🎫 **Ticket**: [{ticket_num}]({ticket_url})" if not ticket.get("suppressed") else "🎫 **Ticket**: Suppressed (Auto-Resolved)"

    return (
        f"### 📋 Executive Snapshot: {workflow_name}\n"
        f"**{outcome_title}**\n"
        f"{outcome_body}\n\n"
        f"**Root Cause**: {rca_cause}\n"
        f"**Action Taken**: {rca_fix}\n\n"
        f"> {rca_sum}\n\n"
        f"**Next Steps**:\n"
        f"• The system will continue to monitor the agent state.\n"
        f"• {ticket_info}\n"
        f"{kb_section}\n"
        f"--- \n"
        f"<details>\n"
        f"<summary>🔍 **Debug Info (Technical Details)**</summary>\n\n"
        f"- **Agent**: {agent_name} ({agent_status})\n"
        f"- **Schedule**: {sched_status}\n"
        f"- **Severity**: {rca.get('severity', 'Medium')} ({severity_emoji})\n"
        f"- **Impact**: {rca.get('estimated_impact', 'Unknown')}\n"
        f"- **Confidence**: {rca.get('confidence', 'Low')} ({confidence_emoji})\n"
        f"</details>"
    )


def _build_escalation_summary(
    workflow_name: str,
    agent_name: str,
    ticket: Dict,
) -> str:
    ticket_num = ticket.get("ticket_number", "N/A")
    ticket_url = ticket.get("url", "N/A")
    return (
        f"🚨 **Escalated — {workflow_name}**\n"
        f"{'─' * 50}\n\n"
        f"I tried restarting the agent **{agent_name}** but wasn't able to bring it back online.\n\n"
        f"A **High priority** ServiceNow ticket has been raised for your IT support team. "
        f"They'll be in touch within your SLA timeframe.\n\n"
        f"🎟️ **Ticket: {ticket_num}**\n"
        f"   URL: {ticket_url}\n\n"
        f"Is there anything else I can help with in the meantime?"
    )


def normalize_outcome(summary: Dict) -> str:
    """Normalizes granular final_status into a standardized outcome state (v3.7)."""
    fs = summary.get("final_status")
    if fs in ("fast_path_ok", "running_ok", "pending_ok"):
        return "healthy"
    if fs in ("recovered", "recovered_from_unknown", "light_recovery_done"):
        return "recovered"
    if fs in ("ticket_created", "ticket_suppressed"):
        return "incident_logged"
    if fs == "escalated_agent":
        return "escalated"
    if fs == "clarification_needed":
        return "clarification_needed"
    return "unknown"


# ===========================================================================
# ─── SOP-BASED RECOVERY FLOW (v3: silent SOP, friendly user messages) ────────
# ===========================================================================

async def run_sop_recovery_flow(
    user_query: str,
    workflow_name: Optional[str],
    workflow_id: Optional[str],
    workflow_params_schema: Optional[List[Dict]],
    collected_params: Dict[str, str],
    session_token: Optional[str],
    stream: MessageCallback = _noop_stream,
    org_code: Optional[str] = None,
    kb_project_id: Optional[str] = None,
    kb_secret: Optional[str] = None,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    conversation_id: Optional[Any] = None,
) -> Dict:
    """
    Executes the full SOP recovery flow silently in the background.

    The `stream` callback receives ONLY friendly, human-readable messages —
    never internal step names, API URLs, field names, or technical jargon.

    All internal details are written to the logger (logs/agentic.log) only.
    """
    summary: Dict[str, Any] = {
        "workflow_name":      workflow_name,
        "workflow_id":        workflow_id,
        "agent_status":       None,
        "agent_name":         None,
        "agent_id":           None,
        "agent_restarted":    False,
        "workflow_status":    None,
        "request_id":         None,
        "schedule_status":    None,
        "schedule_raw":       None,
        "log_result":         None,
        "log_error_code":     None,
        "workflow_restarted": False,
        "new_request_id":     None,
        "ticket":             None,
        "rca":                None,
        "kb_articles":        [],
        "final_status":       None,
        "messages":           [],
    }

    async def emit(msg: str) -> None:
        """Send a user-visible message."""
        await stream(msg)
        summary["messages"].append(msg)

    def log(msg: str) -> None:
        """Internal log only — never shown to user."""
        logger.info(f"[SOP-BG] {msg}")

    # ── Validate workflow name ────────────────────────────────────────────
    if not workflow_name:
        await emit(_user_msg_clarify())
        summary["final_status"] = "clarification_needed"
        return summary

    await emit(_user_msg_start(workflow_name))

    # --- Cool-Down Protection (v3.7) ---
    # Optional logic to prevent immediate re-runs of the same workflow within a short window.

    # --- Light Recovery Mode (v3.5/v3.8) ---
    if RECOVERY_MODE == "light":
        log("[V3.5-SOP-LIGHT] Skipping RCA and SNOW. Light mode active.")
        summary["final_status"] = "light_recovery_done"
        summary["normalized_outcome"] = normalize_outcome(summary)
        await emit(f"✅ Recovery completed for **{workflow_name}**. (Light Mode enabled)")
        return summary

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 1: Check Agent Status
    # ═══════════════════════════════════════════════════════════════════════
    await emit(_user_msg_agent_checking())
    log("Fetching agent details via /monitoring/agents + /inspect ...")

    try:
        agent_details = t4_get_agent_details(org_code=org_code, session_token=session_token)
    except Exception as e:
        log(f"Agent details fetch failed: {e}")
        agent_details = {}

    if agent_details:
        agent_name  = agent_details["agentName"]
        agent_id    = agent_details["agentId"]
        agent_state = agent_details["agentState"]
        summary.update({"agent_name": agent_name, "agent_id": agent_id, "agent_status": agent_state})
        log(f"[SOP-01] done — agent={agent_name}, id={agent_id}, state={agent_state}")

        # v3.8: Sync granular agent details back to user context in DB
        if user_id:
            try:
                upsert_user_context(
                    user_id=user_id,
                    user_name=user_name or "Unknown",
                    conversation_id=conversation_id,
                    org_code=org_code or T4_ORG_CODE,
                    session_token=session_token,
                    bot_id="AE-Bot",
                    agent_id=agent_id,
                    user_workflow=workflow_name
                )
            except Exception as e:
                log(f"User context upsert failed: {e}")
    else:
        agent_name  = "Unknown"
        agent_id    = "Unknown"
        agent_state = "NO_AGENTS"
        summary["agent_status"] = agent_state
        log("No agents returned from API.")

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 2: Restart Agent if not RUNNING
    # ═══════════════════════════════════════════════════════════════════════
    if agent_state in ("STOPPED", "UNKNOWN", "NO_AGENTS"):
        await emit(_user_msg_agent_down(agent_name, agent_state))

        for attempt in range(1, MAX_AGENT_RESTART_ATTEMPTS + 1):
            await emit(_user_msg_agent_restarting(attempt, MAX_AGENT_RESTART_ATTEMPTS))
            log(f"[SOP-02] Agent restart attempt {attempt}/{MAX_AGENT_RESTART_ATTEMPTS} for '{agent_name}'")

            try:
                if not agent_name or agent_name == "Unknown":
                    log("Agent name unknown — cannot restart.")
                    break

                # Use local PowerShell restart exclusively
                if t4_local_restart_agent_service():
                    log(f"Local restart issued. Waiting {AGENT_RESTART_WAIT_SEC}s ...")
                    await asyncio.sleep(AGENT_RESTART_WAIT_SEC)

                    fresh = t4_get_agent_details(org_code=org_code, session_token=session_token, target_agent_name=agent_name)
                    if fresh:
                        new_state = fresh["agentState"]
                        summary["agent_status"] = new_state
                        log(f"Agent state after restart: {new_state}")
                        if new_state == "RUNNING":
                            summary["agent_restarted"] = True
                            agent_state = "RUNNING"
                            await emit(_user_msg_agent_restart_ok())
                            break
                        else:
                            agent_state = new_state
                else:
                    log("SOP: Local restart attempt failed.")

            except Exception as e:
                log(f"Agent restart attempt {attempt} error: {e}")

        if agent_state != "RUNNING":
            await emit(_user_msg_agent_restart_failed())
            log("Agent could not be restarted. Raising escalation ticket.")

            ticket = create_servicenow_ticket(
                workflow_name=workflow_name,
                summary=f"Agent restart failed — {workflow_name}",
                description=(
                    f"Agent '{agent_name}' (ID: {agent_id}) could not be restarted "
                    f"after {MAX_AGENT_RESTART_ATTEMPTS} attempts. Final state: {agent_state}"
                ),
                priority="2",
            )
            summary["ticket"] = ticket
            summary["final_status"] = "escalated_agent"
            await emit(_build_escalation_summary(workflow_name, agent_name, ticket))
            return summary

    else:
        await emit(_user_msg_agent_ok(agent_name))

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 3: Check Workflow Status
    # ═══════════════════════════════════════════════════════════════════════
    if not workflow_id and workflow_name == "Agent Health Check":
        log("Direct agent request — skipping workflow-specific steps.")
        summary["final_status"] = "agent_check_done"
        
        status_emoji = "🟢" if agent_state == "RUNNING" else "🔴"
        id_str = f" (`{agent_id}`)" if agent_id and agent_id != "Unknown" else ""
        
        await emit(
            f"📊 **Agent Status Report**\n"
            f"• **Name**: {agent_name}{id_str}\n"
            f"• **Status**: {status_emoji} **{agent_state}**\n\n"
            f"The agent is currently {'online and healthy' if agent_state == 'RUNNING' else 'down and may need attention'}."
        )
        return summary

    await emit(_user_msg_workflow_checking(workflow_name))
    wf_status  = "Unknown"
    request_id = ""

    try:
        wf_data    = t4_check_workflow_status(workflow_name=workflow_name, org_code=org_code, session_token=session_token)
        wf_status  = wf_data.get("status", "Unknown")
        request_id = str(wf_data.get("automationRequestId") or wf_data.get("requestId") or "")
        summary["workflow_status"] = wf_status
        summary["request_id"]      = request_id
        log(f"Workflow status: {wf_status}, request_id: {request_id}")
    except Exception as e:
        log(f"Workflow status check failed: {e}")

    await emit(_user_msg_workflow_status(workflow_name, wf_status))

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 4: Handle Unknown → try triggering the workflow
    # ═══════════════════════════════════════════════════════════════════════
    if wf_status == "Unknown":
        await emit(_user_msg_workflow_triggering(workflow_name))
        log(f"Workflow status Unknown — attempting to trigger '{workflow_name}'")

        try:
            params_list = [
                {"name": p["name"], "value": collected_params.get(p["name"], ""), "type": p.get("type", "String")}
                for p in (workflow_params_schema or [])
            ]
            new_request_id = t4_execute_workflow(
                workflow_name=workflow_name,
                workflow_id=workflow_id or "",
                params=params_list,
                org_code=org_code,
                session_token=session_token,
            )
            summary["new_request_id"] = new_request_id
            log(f"Triggered. new_request_id={new_request_id}. Waiting {WORKFLOW_RESTART_WAIT_SEC}s ...")
            await asyncio.sleep(WORKFLOW_RESTART_WAIT_SEC)

            poll = t4_poll_status(request_id=new_request_id, org_code=org_code, session_token=session_token)
            wf_status = poll["status"]
            summary["workflow_status"] = wf_status
            log(f"Status after trigger: {wf_status}")

            if wf_status == "Complete":
                summary["workflow_restarted"] = True
                summary["final_status"] = "recovered_from_unknown"
                await emit(_user_msg_workflow_trigger_ok(workflow_name))
                return summary

        except Exception as e:
            log(f"Workflow trigger from Unknown state failed: {e}")

    # ── Already running / pending — nothing to do ──────────────────────────
    if wf_status == "Running":
        summary["final_status"] = "running_ok"
        await emit(_user_msg_running_ok(workflow_name))
        return summary

    if wf_status in ("New", "Execution Started"):
        summary["final_status"] = "pending_ok"
        await emit(f"✅ **{workflow_name}** is queued and will start momentarily. All looks good!")
        return summary

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 5: Check Schedule
    # ═══════════════════════════════════════════════════════════════════════
    if workflow_id:
        await emit(_user_msg_schedule_checking())
        log(f"Checking schedule for workflow_id={workflow_id}")
        try:
            sched = check_agent_schedule_activity(
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                org_code=org_code,
                session_token=session_token,
            )
            summary["schedule_status"] = sched.get("adherence_status")
            summary["schedule_raw"]    = sched
            log(f"Schedule result: status={sched.get('adherence_status')}, delay={sched.get('delay_minutes')} min")
            await emit(_user_msg_schedule_result(sched))
        except Exception as e:
            log(f"Schedule check error: {e}")
    else:
        log("Schedule check skipped — no workflow_id available.")

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 6: Check Logs
    # ═══════════════════════════════════════════════════════════════════════
    has_error_in_logs = False
    log_error_detail  = ""
    log_error_code    = None

    if wf_status in ("Complete", "Failure", "Diverted", "Unknown"):
        await emit(_user_msg_logs_checking())
        log(f"Fetching logs for '{workflow_name}', request_id={request_id or 'N/A'}")

        try:
            log_result = t4_check_log_files(
                workflow_name=workflow_name,
                request_id=request_id or None,
                org_code=org_code,
                session_token=session_token,
            )
            summary["log_result"] = log_result

            err_str = log_result.get("error", "")
            for code in ("400", "404", "500", "403"):
                if code in err_str:
                    log_error_code = code
                    break
            summary["log_error_code"] = log_error_code
            log(f"Log retrieval: success={log_result['success']}, error_code={log_error_code}")

            if not log_result["success"]:
                has_error_in_logs = wf_status == "Failure"
                log_error_detail  = f"Log retrieval failed ({log_error_code or 'unknown'}): {log_result.get('error', '')}"
                log(f"Log retrieval failed: {log_error_detail}")
            else:
                logs = log_result.get("logs", [])
                error_entries = [
                    l for l in (logs if isinstance(logs, list) else [])
                    if any(kw in str(l).lower() for kw in ["error", "exception", "failed", "critical"])
                ]
                if error_entries:
                    has_error_in_logs = True
                    log_error_detail  = str(error_entries[0])[:300]
                    log(f"Log errors found: {log_error_detail[:150]}")
                elif wf_status in ("Failure", "Complete"):
                    has_error_in_logs = True
                    log_error_detail  = "No specific error in logs."
                    log("No specific log errors found but workflow was in Failure/Complete state.")

            await emit(_user_msg_logs_result(has_error_in_logs))

        except Exception as e:
            log(f"Log check exception: {e}")
            has_error_in_logs = wf_status == "Failure"

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 7: Restart Workflow if Failure
    # ═══════════════════════════════════════════════════════════════════════
    restarted_successfully = False

    if wf_status == "Failure":
        log(f"Workflow is in Failure state. Attempting restart (max {MAX_WORKFLOW_RESTART_ATTEMPTS})...")

        for attempt in range(1, MAX_WORKFLOW_RESTART_ATTEMPTS + 1):
            await emit(_user_msg_workflow_restarting(attempt, MAX_WORKFLOW_RESTART_ATTEMPTS))
            log(f"Workflow restart attempt {attempt}/{MAX_WORKFLOW_RESTART_ATTEMPTS}")

            try:
                new_request_id = t4_restart_workflow(
                    workflow_name=workflow_name,
                    workflow_id=workflow_id or "",
                    collected_params=collected_params,
                    workflow_params_schema=workflow_params_schema or [],
                    org_code=org_code,
                    session_token=session_token,
                )
                summary["new_request_id"] = new_request_id
                log(f"Restart issued. new_request_id={new_request_id}. Waiting {WORKFLOW_RESTART_WAIT_SEC}s ...")
                await asyncio.sleep(WORKFLOW_RESTART_WAIT_SEC)

                poll = t4_poll_status(request_id=new_request_id, org_code=org_code, session_token=session_token)
                new_status = poll["status"]
                summary["workflow_status"] = new_status
                log(f"Post-restart status: {new_status}")

                if new_status == "Complete":
                    restarted_successfully = True
                    summary["workflow_restarted"] = True
                    break
                elif new_status == "no_agent":
                    log("No agent available during restart.")
                    break

            except Exception as e:
                log(f"Workflow restart attempt {attempt} error: {e}")

        if restarted_successfully:
            await emit(_user_msg_workflow_restart_ok(workflow_name))
            summary["final_status"] = "recovered"
            # Still do RCA + ticket for record-keeping but don't alarm the user
        else:
            await emit(_user_msg_workflow_restart_failed(workflow_name))
            log("All workflow restart attempts exhausted.")

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 8: Root Cause Analysis (background — not exposed to user)
    # ═══════════════════════════════════════════════════════════════════════
    await emit(_user_msg_rca_running())
    log("Running RCA engine...")

    rca = safe_llm_call(
        perform_rca,
        {"primary_cause": "unknown", "confidence": "Low", "rca_summary": "RCA temporarily unavailable."},
        workflow_name=workflow_name,
        agent_name=summary.get("agent_name"),
        agent_id=summary.get("agent_id"),
        agent_status_initial=summary.get("agent_status"),
        agent_status_after_restart=summary.get("agent_status") if summary.get("agent_restarted") else None,
        agent_restarted=summary.get("agent_restarted", False),
        workflow_status=wf_status,
        workflow_restarted=summary.get("workflow_restarted", False),
        workflow_status_after_restart=summary.get("workflow_status") if summary.get("workflow_restarted") else None,
        schedule_data=summary.get("schedule_raw"),
        log_error_detail=log_error_detail,
        log_retrieval_failed=not (summary.get("log_result") or {}).get("success", False),
        log_error_code=summary.get("log_error_code"),
        restart_attempts_exhausted=not restarted_successfully and wf_status == "Failure",
        user_query=user_query,
    )
    summary["rca"] = rca
    # --- Incident Memory (v3.5) ---
    summary["rca_summary"] = rca.get("rca_summary", "No summary available.")

    log(f"RCA result: cause={rca.get('primary_cause')}, confidence={rca.get('confidence')}, severity={rca.get('severity')}")

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 9: Create ServiceNow Ticket (background — shown only in summary)
    # ═══════════════════════════════════════════════════════════════════════
    await emit(_user_msg_ticket_creating())
    log("Creating ServiceNow ticket...")

    severity_to_priority = {"Critical": "1", "High": "2", "Medium": "3", "Low": "4"}
    priority       = severity_to_priority.get(rca.get("severity", "Medium"), "3")
    priority_label = {"1": "Critical", "2": "High", "3": "Medium", "4": "Low"}.get(priority, "Medium")
    wf_status_final = summary.get("workflow_status", "Unknown")

    description_lines = [
        f"User reported issue with workflow '{workflow_name}'.",
        "",
        "=== INVESTIGATION SUMMARY ===",
        f"  - Agent: {summary['agent_name']} (ID: {summary['agent_id']})",
        f"  - Agent Status: {summary['agent_status']}",
        f"  - Agent Restarted: {'Yes' if summary['agent_restarted'] else 'No'}",
        f"  - Workflow Status: {wf_status_final}",
        f"  - Request ID: {summary['request_id'] or 'N/A'}",
        f"  - Schedule Status: {summary.get('schedule_status', 'Not checked')}",
        f"  - Log Error: {log_error_detail or 'None'}",
        f"  - New Request ID after restart: {summary.get('new_request_id', 'N/A')}",
        "",
        "=== ROOT CAUSE ANALYSIS ===",
        f"  - Primary Cause: {rca.get('primary_cause_label', 'Unknown')} ({rca.get('confidence', 'Low')} confidence)",
        f"  - Detail: {rca.get('primary_cause_detail', 'N/A')}",
        f"  - Contributing Factors: {', '.join(rca.get('contributing_factors', [])) or 'None'}",
        f"  - Severity: {rca.get('severity', 'Medium')}",
        f"  - Impact: {rca.get('estimated_impact', 'Unknown')}",
        f"  - Recommended Fix: {rca.get('recommended_fix', 'Manual investigation required')}",
        f"  - RCA Summary: {rca.get('rca_summary', 'N/A')}",
    ]

    # --- Ticket Suppression (v3.5/v3.8) ---
    skip_ticket = False
    if rca.get("confidence") == "Low" and restarted_successfully:
        log("Ticket Suppression: Low confidence RCA + successful restart = skipping ticket.")
        skip_ticket = True

    if skip_ticket:
        ticket = {"success": True, "ticket_number": "Suppressed (Low Confidence + Resolved)", "url": "N/A", "suppressed": True}
    else:
        ticket = create_servicenow_ticket(
            workflow_name=workflow_name,
            summary=f"[RCA: {rca.get('primary_cause_label', 'Unknown')}] Workflow '{workflow_name}' — {wf_status_final}",
            description="\n".join(description_lines),
            priority=priority,
        )
    summary["ticket"] = ticket
    summary["final_status"] = "ticket_created" if not skip_ticket else "ticket_suppressed"
    log(f"Ticket status: {'Suppressed' if skip_ticket else ticket.get('ticket_number')} (priority {priority_label})")

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 10: Knowledge Base Search
    # ═══════════════════════════════════════════════════════════════════════
    await emit(_user_msg_kb_searching())
    kb_articles: List[Dict] = []

    try:
        kb_query    = f"{workflow_name} {rca.get('primary_cause_label', '')} {wf_status}"
        kb_articles = kb_search(
            query=kb_query,
            top_k=3,
            kb_project_id=kb_project_id,
            kb_secret=kb_secret,
        )
        summary["kb_articles"] = kb_articles
        log(f"KB search returned {len(kb_articles)} articles.")
    except Exception as e:
        log(f"KB search failed: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # FINAL: Build and emit clean user-facing summary
    # ═══════════════════════════════════════════════════════════════════════
    final_msg = _build_final_summary(
        workflow_name=workflow_name,
        summary=summary,
        rca=rca,
        ticket=ticket,
        kb_articles=kb_articles,
        restarted_successfully=restarted_successfully,
        wf_status=wf_status_final,
    )
    await emit(final_msg)

    summary["normalized_outcome"] = normalize_outcome(summary)
    return summary


# ===========================================================================
# ─── DIAGNOSTIC AGENT FUNCTIONS ─────────────────────────────────────────────
# ===========================================================================

def diagnostic_agent_select_tool(user_query: str, candidate_workflows: List[Dict]) -> Dict:
    tools_text = "\n".join(
        f"{i+1}. Name: {wf['workflow_name']}\n"
        f"   ID: {wf['workflow_id']}\n"
        f"   Description: {wf['description']}\n"
        f"   Params: {[p['name'] for p in wf.get('params', [])]}"
        for i, wf in enumerate(candidate_workflows)
    )

    prompt = f"""You are the Diagnostic Agent (READ-ONLY).
Pick the single most appropriate workflow that can CHECK, STATUS, FETCH, or MONITOR.

User query: "{user_query}"

Candidate workflows:
{tools_text}

Reply with valid JSON only:
{{
  "workflow_index": <0-based index>,
  "reason": "<one sentence why this workflow fits>"
}}"""

    raw = vertex_generate(prompt, system_prompt=DIAGNOSTIC_SYSTEM_PROMPT, response_format="json")
    try:
        selection = json.loads(raw)
        idx = int(selection.get("workflow_index", 0))
        return candidate_workflows[idx] if idx < len(candidate_workflows) else candidate_workflows[0]
    except Exception:
        return candidate_workflows[0]


def diagnostic_agent_check_params(workflow: Dict, collected_params: Dict[str, str]) -> Tuple[List[Dict], List[Dict]]:
    required: List[Dict] = workflow.get("params", [])
    missing: List[Dict] = [p for p in required if not collected_params.get(p["name"])]
    return required, missing


def diagnostic_agent_ask_params(workflow_name: str, missing_params: List[Dict]) -> str:
    param_lines = "\n".join(f"  • {p['name']}: {p.get('description', 'Required parameter')}" for p in missing_params)
    return (
        f"To run the **{workflow_name}** diagnostic check, I need a couple of details:\n"
        f"{param_lines}\n\nPlease share these and I'll get right on it!"
    )


def diagnostic_agent_extract_params(user_message: str, pending_param_names: List[str]) -> Dict[str, Optional[str]]:
    if not pending_param_names:
        return {}

    prompt = f"""Extract parameter values from the user message.
Return valid JSON only.

Parameters needed: {pending_param_names}
User message: "{user_message}"

Return: {{"param_name": "value_or_null", ...}}"""

    raw = vertex_generate(prompt, system_prompt=DIAGNOSTIC_SYSTEM_PROMPT, response_format="json")
    try:
        return json.loads(raw)
    except Exception:
        return {p: None for p in pending_param_names}


async def diagnostic_agent_execute(workflow: Dict, collected_params: Dict[str, str], session_token: Optional[str] = None) -> Dict:
    params_list = [
        {"name": p["name"], "value": collected_params.get(p["name"], ""), "type": p.get("type", "String")}
        for p in workflow.get("params", [])
    ]
    request_id = t4_execute_workflow(
        workflow_name=workflow["workflow_name"],
        workflow_id=workflow["workflow_id"],
        params=params_list,
        session_token=session_token,
    )
    result = t4_poll_status(request_id, session_token=session_token)
    # Return friendly message
    status = result.get("status", "Unknown")
    if status == "Complete":
        result["message"] = f"✅ Diagnostic check for **{workflow['workflow_name']}** completed successfully!"
    elif status == "Failure":
        result["message"] = f"⚠️ The diagnostic check for **{workflow['workflow_name']}** encountered an issue. Please review the logs."
    else:
        result["message"] = f"ℹ️ Diagnostic check status: **{status}**."
    return result


# ===========================================================================
# ─── REMEDIATION AGENT FUNCTIONS ────────────────────────────────────────────
# ===========================================================================

def remediation_agent_select_tool(user_query: str, candidate_workflows: List[Dict]) -> Dict:
    tools_text = "\n".join(
        f"{i+1}. Name: {wf['workflow_name']}\n"
        f"   ID: {wf['workflow_id']}\n"
        f"   Description: {wf['description']}\n"
        f"   Params: {[p['name'] for p in wf.get('params', [])]}"
        for i, wf in enumerate(candidate_workflows)
    )

    prompt = f"""You are the Remediation Agent (WRITE/ACTION capable).
Pick the single most appropriate workflow that can FIX, PROCESS, EXECUTE, RECONCILE, or RESTART.

User query: "{user_query}"

Candidate workflows:
{tools_text}

Reply with valid JSON only:
{{
  "workflow_index": <0-based index>,
  "reason": "<one sentence why>"
}}"""

    raw = vertex_generate(prompt, system_prompt=REMEDIATION_SYSTEM_PROMPT, response_format="json")
    try:
        selection = json.loads(raw)
        idx = int(selection.get("workflow_index", 0))
        return candidate_workflows[idx] if idx < len(candidate_workflows) else candidate_workflows[0]
    except Exception:
        return candidate_workflows[0]


def remediation_agent_check_params(workflow: Dict, collected_params: Dict[str, str]) -> Tuple[List[Dict], List[Dict]]:
    required: List[Dict] = workflow.get("params", [])
    missing: List[Dict] = [p for p in required if not collected_params.get(p["name"])]
    return required, missing


def remediation_agent_ask_params(workflow_name: str, missing_params: List[Dict]) -> str:
    """Use LLM to generate a natural, friendly param collection message."""
    friendly_name = humanize_workflow_name(workflow_name)
    param_descriptions = "\n".join(
        f"- {p['name']}: {p.get('description', 'a required value')}"
        for p in missing_params
    )
    prompt = f"""You are a friendly automation assistant about to run the task: '{friendly_name}'.
Ask the user to provide the following required inputs in a warm, conversational tone.
List each item clearly with a bullet point. End with an encouraging note.
Do NOT mention JSON, APIs, workflow IDs, or any technical terms. Keep it under 6 lines.

Required inputs:
{param_descriptions}

Write the message now:"""
    try:
        return vertex_generate(prompt, system_prompt=RESPONDER_SYSTEM_PROMPT, temperature=0.4)
    except Exception:
        # Fallback to static message
        param_lines = "\n".join(f"  • {p['name']}: {p.get('description', 'Required')}" for p in missing_params)
        return (
            f"I'm ready to help with **{friendly_name}**! Just need a few details first:\n"
            f"{param_lines}\n\nShare those and I'll take care of the rest."
        )


def remediation_agent_extract_params(user_message: str, pending_param_names: List[str]) -> Dict[str, Optional[str]]:
    if not pending_param_names:
        return {}

    prompt = f"""Extract parameter values from the user message.
Return valid JSON only.

Parameters needed: {pending_param_names}
User message: "{user_message}"

Return: {{"param_name": "value_or_null", ...}}"""

    raw = vertex_generate(prompt, system_prompt=REMEDIATION_SYSTEM_PROMPT, response_format="json")
    try:
        return json.loads(raw)
    except Exception:
        return {p: None for p in pending_param_names}


def remediation_agent_confirm(workflow_name: str, collected_params: Dict[str, str]) -> str:
    friendly_name = humanize_workflow_name(workflow_name)
    param_summary = "\n".join(f"  • {k}: **{v}**" for k, v in collected_params.items())
    return (
        f"🤖 Almost there! Before I proceed, let me confirm the details with you:\n\n"
        f"**Task:** {friendly_name}\n"
        f"**What I'll use:**\n{param_summary}\n\n"
        f"Does everything look right? Reply **Yes** to proceed or **No** to cancel. 😊"
    )


async def remediation_agent_execute(
    workflow: Dict,
    collected_params: Dict[str, str],
    user_confirmation: str,
    session_token: Optional[str] = None,
    output_file_name: Optional[str] = None,
    download_output: bool = False,
    output_save_path: str = "./remediation_output.xlsx",
) -> Dict:
    confirmed = user_confirmation.strip().upper() in ("YES", "Y", "CONFIRM")
    if not confirmed:
        return {
            "status":          "cancelled",
            "request_id":      None,
            "file_id":         None,
            "row_count":       None,
            "downloaded_file": None,
            "message":         "No problem — action cancelled. Nothing was changed.",
        }

    params_list = [
        {"name": p["name"], "value": collected_params.get(p["name"], ""), "type": p.get("type", "String")}
        for p in workflow.get("params", [])
    ]

    request_id = t4_execute_workflow(
        workflow_name=workflow["workflow_name"],
        workflow_id=workflow["workflow_id"],
        params=params_list,
        session_token=session_token,
    )

    poll = t4_poll_status(request_id, output_file_name=output_file_name, session_token=session_token)

    status_messages = {
        "Complete": f"✅ **{workflow['workflow_name']}** completed successfully! Check your email for the output.",
        "Failure":  f"❌ **{workflow['workflow_name']}** encountered an issue. Please check your input and try again.",
        "no_agent": "⚠️ No automation agent was available to run this. Please contact your administrator.",
    }

    result = {
        "status":          poll["status"],
        "request_id":      request_id,
        "file_id":         poll.get("file_id"),
        "row_count":       poll.get("row_count"),
        "downloaded_file": None,
        "message":         status_messages.get(poll["status"], f"ℹ️ Status: {poll['status']}."),
    }

    if download_output and poll["status"] == "Complete" and poll.get("file_id"):
        try:
            saved = t4_download_file(
                file_id=poll["file_id"],
                request_id=request_id,
                save_path=output_save_path,
                session_token=session_token,
            )
            result["downloaded_file"] = saved
        except Exception as e:
            logger.warning(f"Download failed: {e}")

    return result


# ===========================================================================
# ─── INFORMATIONAL — FULL WORKFLOW LIST ──────────────────────────────────────
# ===========================================================================

def format_full_workflow_list(all_workflows: List[Dict]) -> str:
    total = len(all_workflows)
    lines = [f"📋 You have **{total} workflow(s)** configured in AutomationEdge:\n"]

    for i, wf in enumerate(all_workflows, 1):
        param_count = len(wf.get("params", []))
        param_str   = f"{param_count} param(s)" if param_count else "no params"
        desc        = wf["description"][:80] + "..." if len(wf["description"]) > 80 else wf["description"]
        lines.append(
            f"  {i:3}. **{wf['workflow_name']}**  _(ID: {wf['workflow_id']} | {param_str})_\n"
            + (f"        {desc}\n" if desc else "")
        )

    lines.append(f"\n**Total: {total} workflows**")
    return "\n".join(lines)


# ===========================================================================
# ─── MAIN ORCHESTRATOR ──────────────────────────────────────────────────────
# ===========================================================================

async def _run_agentic_flow_internal(
    user_query: str,
    collected_params: Optional[Dict[str, str]] = None,
    user_confirmation: Optional[str] = None,
    workflow_selected: Optional[Dict] = None,
    current_intent: Optional[str] = None,
    previous_state: Optional[str] = None,
    session_token: Optional[str] = None,
    tried_ids: Optional[List[str]] = None,
    top_k: int = 5,
    download_output: bool = True,
    output_save_path: str = "./output.xlsx",
    stream: MessageCallback = _noop_stream,
    kb_project_id: Optional[str] = None,
    kb_secret: Optional[str] = None,
    history: str = "",
    last_recovery_summary: Optional[str] = None,
) -> Dict:
    """
    Final Clean Flow:
    User Query -> Intent Classification -> (Natural -> Direct LLM)
    -> (Diagnostic -> Embedding -> SOP Top 3 -> Workflow Top 5 -> LLM Analysis -> Confidence Check -> Workflow Trigger -> Logging)
    """
    collected_params = collected_params or {}
    tried_ids = tried_ids or []
    
    output: Dict[str, Any] = {
        "intent":            current_intent or "diagnostic",
        "state":             "analyzing",
        "status":            "Performing SOP analysis...",
        "response":          "",
        "workflow_selected": workflow_selected,
        "collected_params":  collected_params,
        "tried_ids":         tried_ids,
        "structured_data":   None,
    }

    # Step 1: User Query (input param)
    
    # Step 2: Intent Classification — RAG-informed routing
    if not current_intent:
        # Embed query and do a quick RAG search to give relevant SOP/workflow context to the router
        _wf_catalogue = None
        _sop_catalogue = None
        try:
            _route_embedding = embed_text(user_query)
            _sop_hits = search_sops_vector(_route_embedding, top_k=3, threshold=0.1)
            _wf_hits  = search_workflows_vector(_route_embedding, top_k=3)
            if _sop_hits:
                _sop_catalogue = "\n".join([f"- [{s['reference_id']}] {s['title']}: {(s['content'] or '')[:100]}" for s in _sop_hits])
            if _wf_hits:
                _wf_catalogue  = "\n".join([f"- {w['workflow_name']} (ID:{w['workflow_id']}): {(w['description'] or '')[:100]}" for w in _wf_hits])
            logger.info(f"RAG routing context: {len(_sop_hits)} SOPs, {len(_wf_hits)} workflows found.")
        except Exception as _e:
            logger.warning(f"RAG context fetch for routing failed (non-critical): {_e}")

        intent = master_agent_route(
            user_query,
            workflow_catalogue=_wf_catalogue,
            sop_catalogue=_sop_catalogue,
        )
        output["intent"] = intent
    else:
        intent = current_intent
        output["intent"] = intent


    # 🔹 Shortcut: If a workflow is already selected, skip RAG and go to validation
    if workflow_selected:
        # Re-verify params or handle confirmation
        selected_wf_obj = workflow_selected
        wf_name = selected_wf_obj["workflow_name"]
        wf_id = selected_wf_obj["workflow_id"]
        # Maintain intelligence context
        analysis = output.get("structured_data") or {
            "issue_summary": "Continuing previous request",
            "selected_workflow": wf_name,
            "workflow_id": wf_id,
            "confidence_score": 0.9,
            "matched_sop_reference": "N/A"
        }
        confidence = float(analysis.get("confidence_score", 0.9))
        goto_validation = True
    else:
        goto_validation = False
        analysis = None # Initialized below during RAG

    # 🔹 Case 1: Natural Query (only if no workflow active)
    if intent == "natural" and not goto_validation:
        # --- Conversation Memory (v3.5/v3.8) ---
        last_rca = last_recovery_summary
        if any(x in user_query.lower() for x in ["what happened", "previous incident", "history", "last error", "last failure"]):
            if last_rca:
                response = f"Sure! Regarding the previous incident: **{last_rca}**. Is there anything specific you'd like to know more about?"
            else:
                response = "I don't have a record of a recent incident in this session. However, I can check the system logs if you're experiencing an issue now."
        else:
            response = master_agent_respond(user_query, history=history) or "I'm here to help! Could you please tell me what you'd like to do?"
            
        output["response"] = response
        output["state"] = "done"
        log_interaction(user_query, "natural", response)
        return output

    # 🔹 Case 3: Agent Status / Restart
    if intent == "agent_status" and not goto_validation:
        await stream("🔍 Let me check your automation agent's health right now...")
        try:
            agent_info = t4_get_agent_details(session_token=session_token)
        except Exception as _ae:
            logger.warning(f"agent_status: t4_get_agent_details failed: {_ae}")
            agent_info = {}

        if not agent_info:
            msg = (
                "⚠️ I wasn't able to reach the T4 system right now. "
                "Please check your network connection or try again in a moment."
            )
            output["response"] = msg
            output["state"] = "done"
            log_interaction(user_query, "agent_status", msg)
            return output

        _aname  = agent_info.get("agentName", "your agent")
        _astate = agent_info.get("agentState", "UNKNOWN").upper()
        _is_ok  = _astate in ("CONNECTED", "RUNNING", "ACTIVE")

        if _is_ok:
            status_msg = (
                f"✅ Good news! **{_aname}** is currently **{_astate.title()}** and working normally. "
                f"No action needed. 😊"
            )
            output["response"] = status_msg
            output["state"] = "done"
            log_interaction(user_query, "agent_status", status_msg)
            return output
        else:
            # Agent is down — provide manual guidance with clear steps
            msg = (
                f"⚠️ **{_aname}** is currently **Stopped**. 😟\n\n"
                f"To get it back online, please follow these steps:\n\n"
                f"1. Go to your **AutomationEdge Agent** installation folder.\n"
                f"2. Open the **bin** directory.\n"
                f"3. Find and double-click **startup.bat** to start the agent.\n\n"
                "Once started, it will take a moment to reconnect to the T4 system."
            )
            
            output["response"] = msg
            output["state"] = "done"
            log_interaction(user_query, "agent_status", msg)
            return output

    # 🔹 Case 3.5: Informational (v3.5/v3.8)
    if intent == "informational" and not goto_validation:
        await stream("📋 Let me fetch the list of available automation workflows for you...")
        try:
            wfs = t4_fetch_all_workflows(session_token=session_token)
            if not wfs:
                msg = "I currently don't see any active automation workflows in the system."
            else:
                # Group by name for clarity
                wf_list = "\n".join([f"• **{humanize_workflow_name(wf['workflow_name'])}**" for wf in wfs[:15]])
                msg = f"I've found {len(wfs)} workflows. Here are the most relevant ones:\n\n{wf_list}\n\nWould you like me to run or check the status of any of these?"
        except Exception as _e:
            logger.error(f"informational: fetch failed: {_e}")
            msg = "I'm having trouble retrieving the workflow list right now. Please try again in a moment."
            
        output["response"] = msg
        output["state"] = "done"
        log_interaction(user_query, "informational", msg)
        return output

    # 🔹 Case 4: SOP Recovery (Transparent)
    if intent == "recovery" and not goto_validation:
        await stream("🔍 I'm looking into that right now. Checking automation logs and agent health...")
        
        # We try to find a matching workflow first to provide context to the SOP
        query_embedding = embed_text(user_query)
        wf_matches = search_workflows_vector(query_embedding, top_k=1)
        best_wf = wf_matches[0] if wf_matches else None
        
        # Execute the full recovery flow
        recovery_summary = await run_sop_recovery_flow(
            user_query=user_query,
            workflow_name=best_wf["workflow_name"] if best_wf else "Unknown",
            workflow_id=best_wf["workflow_id"] if best_wf else None,
            workflow_params_schema=best_wf.get("params") if best_wf else None,
            collected_params=collected_params,
            session_token=session_token,
            stream=stream,
            kb_project_id=kb_project_id,
            kb_secret=kb_secret,
            user_id=_get_state("user_id"), # We should store this in state earlier
            user_name=_get_state("user_name"),
            conversation_id=_get_state("conversation_id")
        )
        
        # Save summary for future "What happened?" queries
        aistudio_conv_state.add_conv_input_as_param("last_recovery_summary", recovery_summary.get("normalized_outcome", "unknown"))
        
        output["response"] = recovery_summary["messages"][-1] if recovery_summary["messages"] else "I've completed the investigation. Please check the logs for details."
        output["state"] = "done"
        log_interaction(user_query, "recovery", output["response"])
        return output
    # 🔹 Case 2: Diagnostic / Remediation
    if not goto_validation:
        # Step 3: Generate Embedding
        query_embedding = embed_text(user_query)
        
        # Step 4: SOP RAG Search (Top 3)
        sop_matches = search_sops_vector(query_embedding, top_k=3, threshold=0.1) # Acceptable threshold
        
        if not sop_matches:
            await stream("ℹ️ I couldn't find a matching SOP for this request. Could you please clarify or provide more details?")
            output["response"] = "Clarification needed: No matching SOP found."
            log_interaction(user_query, intent, output["response"])
            return output

        # Step 5: Workflow RAG Search (Top 5)
        workflow_matches = search_workflows_vector(query_embedding, top_k=5)
        
        if not workflow_matches:
            await stream("⚠️ No suitable workflows were found to handle this request.")
            output["response"] = "No workflows found."
            log_interaction(user_query, intent, output["response"])
            return output

        # ── Multi-Agent Flow for DIAGNOSTIC intent ───────────────────────────
        # For diagnostic queries, route through DiagnosticAgent + CoderAgent.
        # Remediation queries continue to the existing LLM analysis below.
        if intent == "diagnostic" and MULTI_AGENT_ENABLED:
            sop_text_ctx  = "\n".join([f"[{s['reference_id']}] {s['title']}: {s['content'][:200]}" for s in sop_matches])
            wf_text_ctx   = "\n".join([f"{w['workflow_name']}: {w['description']}" for w in workflow_matches])
            context_bag   = {
                "user_query":        user_query,
                "sop_context":       sop_text_ctx,
                "workflow_context":  wf_text_ctx,
            }

            ma_result = await run_multi_agent_flow(
                user_query=user_query,
                issue_summary=user_query,
                context_bag=context_bag,
                vertex_generate_fn=vertex_generate,
                stream=stream,
                db_conn_fn=get_db_conn,
                t4_monitoring_fn=t4_get_agent_monitoring,
            )

            if ma_result["status"] == "needs_user_input":
                # Ask the user for the missing information
                questions = ma_result.get("user_questions", [])
                q_text = "\n".join([f"• {q}" for q in questions])
                response_msg = (
                    f"🔍 **To complete my analysis, I need a bit more information:**\n{q_text}\n\n"
                    f"Please provide these details and I'll continue the diagnosis."
                )
                output["response"] = response_msg
                output["state"] = "need_params"
                log_interaction(user_query, intent, response_msg)
                return output

            if ma_result["status"] in ("resolved", "escalate"):
                output["response"] = ma_result["result"]
                output["state"] = "done"

                # If Diagnostic Agent says remediation is needed → hand over to workflow engine
                if ma_result.get("next_agent") == "remediation" and workflow_matches:
                    # Fall through to the existing confidence-based workflow execution below
                    # by building a synthetic analysis object
                    best_wf = workflow_matches[0]
                    analysis = {
                        "issue_summary":          ma_result["result"],
                        "selected_workflow":      best_wf["workflow_name"],
                        "workflow_id":            best_wf["workflow_id"],
                        "confidence_score":       ma_result["metadata"].get("confidence", 0.8),
                        "matched_sop_reference":  sop_matches[0]["reference_id"] if sop_matches else "N/A",
                        "sop_analysis_steps":     ["Multi-agent diagnostic completed."],
                    }
                    output["structured_data"] = analysis
                    confidence    = float(analysis["confidence_score"])
                    wf_id         = analysis["workflow_id"]
                    wf_name       = analysis["selected_workflow"]
                    selected_wf_obj = next((w for w in workflow_matches if w["workflow_id"] == wf_id), workflow_matches[0])
                    output["workflow_selected"] = selected_wf_obj
                    # Skip the LLM analysis block and go directly to confidence validation
                    goto_validation = True
                else:
                    log_interaction(user_query, intent, output["response"])
                    return output

        # ── LLM Analysis for REMEDIATION (or Diagnostic fallback) ─────────────
        selected_wf_obj = None

        # Step 6: LLM Analysis (Step 7: Structured Output)

        sop_text = "\n".join([f"- [{s['reference_id']}] {s['title']}: {s['content'][:200]}" for s in sop_matches])
        wf_text = "\n".join([f"- {w['workflow_name']} (ID: {w['workflow_id']}): {w['description']}" for i, w in enumerate(workflow_matches)])
        
        prompt = f"""User Query: "{user_query}"

Top 3 SOP Matches:
{sop_text}

Top 5 Workflow Candidates:
{wf_text}

TASK:
1. Analyze the user query against the SOPs.
2. Select the most appropriate workflow ID from the candidates.
3. Provide a confidence score (0.0 to 1.0).
4. Provide a list of "sop_analysis_steps" summarizing your analysis (3-5 steps).
5. Return ONLY a valid JSON object matching the requested schema.

CRITICAL: Do not include markdown code blocks. Do not include newlines inside JSON string values.
"""

        try:
            analysis_text = ""
            try:
                analysis_text = vertex_generate(prompt, system_prompt=ANALYSIS_SYSTEM_PROMPT, response_format="json")
                logger.info(f"LLM Analysis Raw Response: {analysis_text}")
                
                cleaned_json = _clean_json(analysis_text)
                analysis = json.loads(cleaned_json)
            except Exception as e:
                logger.error(f"LLM Analysis failed: {e}. Raw response: {analysis_text[:200]}...")
                # 🔹 Graceful Fallback: Default to low confidence to avoid crashing the whole flow
                analysis = {
                    "issue_summary": f"Unexpected error during analysis: {str(e)}",
                    "confidence_score": 0.1,
                    "selected_workflow": "None",
                    "workflow_id": "None",
                    "sop_analysis_steps": ["Encountered a technical issue while parsing the AI response.", "Falling back to manual support escalation."]
                }

            # Step 9: Process Analysis
            output["structured_data"] = analysis
            confidence = float(analysis.get("confidence_score", 0))
            wf_id = analysis.get("workflow_id")
            # Convert internal name (e.g. WF_add_employee) to human-friendly (Add Employee)
            wf_name = humanize_workflow_name(analysis.get("selected_workflow", ""))
            _raw_wf_name = analysis.get("selected_workflow", "")
            # Match analysis back to a full workflow object (use raw name, not humanized)
            selected_wf_obj = next((w for w in workflow_matches if w['workflow_id'] == wf_id), None)
            if not selected_wf_obj:
                selected_wf_obj = next((w for w in workflow_matches if w['workflow_name'] == _raw_wf_name), workflow_matches[0])
            
            # 🔹 Stream thinking steps for transparency
            analysis_steps = analysis.get("sop_analysis_steps", [])
            if not analysis_steps:
                # Fallback if LLM missed the steps field
                analysis_steps = [
                    f"Analyzed query context for matching Standard Operating Procedures...",
                    f"Validated best matching workflow: **{wf_name}**"
                ]
            
            logger.info(f"Streaming analysis steps: {analysis_steps}")
            
            # Update output with analysis data
            output["structured_data"] = analysis
        except Exception as e:
            logger.error(f"LLM Analysis failed: {e}. Raw response: {raw_analysis[:200]}...")
            output["response"] = "I encountered an error while analyzing your request. Please try rephrasing or contact support."
            log_interaction(user_query, intent, output["response"], {"error": str(e)})
            return output

    # Step 8: Confidence Validation
    
    output["workflow_selected"] = selected_wf_obj

    if confidence >= 0.8:
        # High confidence — check params, then ask for confirmation before executing

        # Check for params
        _, missing = remediation_agent_check_params(selected_wf_obj, collected_params)
        if missing:
            output["state"] = "need_params"
            output["response"] = remediation_agent_ask_params(wf_name, missing)
            return output

        # Check if user already confirmed these params
        is_param_yes = user_confirmation and user_confirmation.lower().strip() in ("yes", "y", "sure", "proceed", "go ahead", "do it", "confirm", "ok", "okay")
        is_param_no  = user_confirmation and user_confirmation.lower().strip() in ("no", "n", "stop", "cancel", "don't", "nope")

        if is_param_no:
            output["response"] = "👍 No problem at all! I've cancelled this action — nothing was changed. Let me know whenever you're ready to try again!"
            output["state"] = "done"
            output["workflow_selected"] = None
            log_interaction(user_query, intent, "User cancelled at param confirmation")
            return output

        if is_param_yes:
            # User confirmed — execute
            exec_result = await remediation_agent_execute(
                workflow=selected_wf_obj,
                collected_params=collected_params,
                user_confirmation="YES",
                session_token=session_token,
                download_output=download_output,
                output_save_path=output_save_path
            )
            output["response"] = exec_result["message"]
            output["state"] = "done"
            # 🔹 v3.9: Clear sticky state on successful execution to prevent repetition
            output["workflow_selected"] = None
            output["collected_params"] = {}
            log_interaction(user_query, intent, output["response"], analysis, confidence, wf_id)
            return output

        # No confirmation yet — show details and ask
        param_lines = "\n".join([f"• **{k}**: {v}" for k, v in collected_params.items()])
        confirm_msg = (
            f"✅ **Perfect! Here's a summary for **{wf_name}**:**\n{param_lines}\n\n"
            f"💡 _Need to change anything? Just type the correction (e.g. 'salary is 50000') or reply **Yes** to proceed, **No** to cancel._"
        )
        output["state"] = "need_param_confirm"
        output["response"] = confirm_msg
        log_interaction(user_query, intent, "Awaiting param confirmation", analysis, confidence, wf_id)

        
    elif confidence >= 0.5:
        # Medium confidence — handle workflow confirmation first, then param confirmation
        is_yes = user_confirmation and user_confirmation.lower() in ("yes", "y", "sure", "proceed", "go ahead", "do it", "confirm", "ok", "okay")
        is_no  = user_confirmation and user_confirmation.lower() in ("no", "n", "stop", "cancel", "don't", "nope")

        if is_yes:
            # Check for params
            _, missing = remediation_agent_check_params(selected_wf_obj, collected_params)
            if missing:
                output["state"] = "need_params"
                output["response"] = remediation_agent_ask_params(wf_name, missing)
                return output

            # Execute directly (user already confirmed workflow + params via 'yes')
            exec_result = await remediation_agent_execute(
                workflow=selected_wf_obj,
                collected_params=collected_params,
                user_confirmation="YES",
                session_token=session_token,
                download_output=download_output,
                output_save_path=output_save_path
            )
            output["response"] = exec_result["message"]
            log_interaction(user_query, intent, output["response"], analysis, confidence, wf_id)
            output["state"] = "done"

        elif is_no:
            output["response"] = "I've cancelled the suggested action. How else can I help you?"
            output["state"] = "done"
            output["workflow_selected"] = None
            output["collected_params"] = {}
            log_interaction(user_query, intent, "User cancelled suggestion")
            return output

        else:
            # First time asking
            output["state"] = "need_confirm"
            output["response"] = remediation_agent_confirm(wf_name, collected_params)
            log_interaction(user_query, intent, "Awaiting user confirmation", analysis, confidence, wf_id)
        
    else:
        # Low confidence -> Escalate to support team
        
        # 🔹 Extract dynamic ticketing metadata from LLM analysis
        suggested_cat = analysis.get("suggested_category", "Software")
        suggested_grp = analysis.get("suggested_assignment_group", "IT Support")
        
        ticket = create_servicenow_ticket(
            workflow_name=wf_name or "Unknown",
            summary=f"Low Confidence Escalation: {user_query[:50]}",
            description=f"User Query: {user_query}\n\nLLM Analysis: {json.dumps(analysis, indent=2)}",
            category=suggested_cat,
            assignment_group=suggested_grp
        )
        output["response"] = f"Your request has been escalated. Ticket Reference: {ticket.get('ticket_number', 'N/A')}"
        log_interaction(user_query, intent, output["response"], analysis, confidence, wf_id)

    return output


# ===========================================================================
# ─── AI STUDIO ENTRY POINT ──────────────────────────────────────────────────
# ===========================================================================

async def run_agentic_flow(
    context: TurnContext,
    dialog_name: str,
    aistudio_conv_state: AIStudioConvState,
    aistudio_user_state: AIStudioUserState,
    stream: MessageCallback = _noop_stream,
) -> Dict:
    """
    AI Studio entry point.

    State is persisted in aistudio_conv_state (NOT a database) across turns:
      - collected_params     → parameters gathered so far for the current workflow
      - workflow_selected    → the workflow being acted on
      - current_intent       → diagnostic / remediation / recovery / etc.
      - tried_workflow_ids   → workflow IDs that have already been tried
      - state                → need_params / need_confirm / done / error
    """

    def _get_state(key):
        return (
            aistudio_conv_state.get_dialog_input_as_param(dialog_name, key) or
            aistudio_conv_state.get_conv_input_as_param(key)
        )

    collected_params_raw  = _get_state("collected_params")
    collected_params      = json.loads(collected_params_raw) if collected_params_raw else {}
    workflow_selected_raw = _get_state("workflow_selected")
    workflow_selected     = json.loads(workflow_selected_raw) if workflow_selected_raw else None
    current_intent        = _get_state("current_intent")
    previous_state        = _get_state("state")
    session_token         = aistudio_user_state.get_user_input_as_param("t4_token") or os.getenv("T4_SESSION_TOKEN")
    tried_ids_raw         = _get_state("tried_workflow_ids")
    tried_ids             = json.loads(tried_ids_raw) if tried_ids_raw else []

    kb_project_id = aistudio_conv_state.get_conv_input_as_param("kb_project_id") or KB_PROJECT_ID
    kb_secret     = aistudio_conv_state.get_conv_input_as_param("kb_project_secret") or KB_SECRET

    # 🔹 CRITICAL: If resetting or starting fresh, clear sticky state to force re-analysis
    if previous_state in ("done", "error", None, ""):
        logger.info(f"Resetting sticky state (previous_state={previous_state}) for fresh query.")
        workflow_selected = None
        current_intent = None
        collected_params = {}
        # Clear from conversation state persistence
        aistudio_conv_state.add_conv_input_as_param("workflow_selected", None)
        aistudio_conv_state.add_conv_input_as_param("current_intent", None)
        aistudio_conv_state.add_conv_input_as_param("collected_params", "{}")
        aistudio_conv_state.add_conv_input_as_param("state", None)
        aistudio_conv_state.add_dialog_input_as_param(dialog_name, "workflow_selected", None)
        aistudio_conv_state.add_dialog_input_as_param(dialog_name, "current_intent", None)
        aistudio_conv_state.add_dialog_input_as_param(dialog_name, "collected_params", "{}")
        aistudio_conv_state.add_dialog_input_as_param(dialog_name, "state", None)

    # 🔹 v3.9: Intent Switcher (Break out of sticky state)
    # If the user is NOT finished but types a new high-level query, reset state to handle the new request.
    user_query_for_routing = ""
    if hasattr(context, "activity") and hasattr(context.activity, "text"):
        user_query_for_routing = context.activity.text or ""

    if previous_state and previous_state not in ("done", "error") and user_query_for_routing:
        q_norm = user_query_for_routing.lower().strip()
        # Don't reset if it's a simple confirmation or a closure
        is_confirmation = q_norm in ("yes", "no", "y", "n", "ok", "okay", "cancel", "stop", "reset")
        
        if not is_confirmation:
            # Check if this new query implies a different intent using the router
            new_intent = master_agent_route(user_query_for_routing)
            
            # If the new intent is high-level (not natural/diagnostic/remediation if they were current)
            # OR if it's a different CATEGORY of intent, we reset.
            is_switch = False
            if new_intent in ("agent_status", "informational", "recovery"):
                is_switch = True
            elif current_intent and new_intent != current_intent and new_intent != "natural":
                is_switch = True
                
            if is_switch:
                logger.info(f"Intent Switcher: Detected new intent '{new_intent}' vs old '{current_intent}'. Resetting state.")
                workflow_selected = None
                current_intent = None
                collected_params = {}
                previous_state = None
                # Clear persistence
                aistudio_conv_state.add_conv_input_as_param("workflow_selected", None)
                aistudio_conv_state.add_conv_input_as_param("current_intent", None)
                aistudio_conv_state.add_conv_input_as_param("collected_params", "{}")
                aistudio_conv_state.add_conv_input_as_param("state", None)
                aistudio_conv_state.add_dialog_input_as_param(dialog_name, "workflow_selected", None)
                aistudio_conv_state.add_dialog_input_as_param(dialog_name, "current_intent", None)
                aistudio_conv_state.add_dialog_input_as_param(dialog_name, "collected_params", "{}")
                aistudio_conv_state.add_dialog_input_as_param(dialog_name, "state", None)

    if workflow_selected and not current_intent:
        current_intent = workflow_selected.get("intent_type")

    user_query = ""
    if hasattr(context, "activity") and hasattr(context.activity, "text"):
        user_query = context.activity.text or ""
        activity = context.activity
        user_id = activity.from_property.id
        user_name = activity.from_property.name
        conversation_id = activity.conversation.id
        bot_id = activity.recipient.id
        session_id = hash(conversation_id)
    else:
        user_query = str(context)
        user_id = "anonymous"
        user_name = "Anonymous"
        conversation_id = "unknown"
        bot_id = "bot"
        session_id = 0

    # v3.8: Save user message (FIRST thing)
    msg_id = None
    try:
        msg_id = save_message(
            session_id=session_id,
            sender=user_name,
            message_type="user",
            message_content=user_query,
            metadata={
                "channel": "teams" if hasattr(context, "activity") else "other",
                "user_id": user_id,
            },
        )
    except Exception as e:
        logger.error(f"DB write failed (user message): {e}")

    active_conversation_id = msg_id

    # v3.8: Persist metadata for multi-turn use
    _save_state = lambda k, v: (aistudio_conv_state.add_dialog_input_as_param(dialog_name, k, v), aistudio_conv_state.add_conv_input_as_param(k, v))
    _save_state("user_id", user_id)
    _save_state("user_name", user_name)
    _save_state("conversation_id", active_conversation_id)

    # v3.8: Upsert user context
    try:
        upsert_user_context(
            user_id=user_id,
            user_name=user_name,
            conversation_id=active_conversation_id,
            org_code=T4_ORG_CODE,
            session_token=session_token,
            bot_id=bot_id
        )
    except Exception as e:
        logger.error(f"DB write failed (user context): {e}")

    # Wrap context.send_activity for real-time updates
    async def ai_studio_stream(msg: str) -> None:
        logger.info(f"AI Studio Stream Target: {msg}")
        try:
            if hasattr(context, "send_activity"):
                # 🔹 Step 1: Send typing indicator to trigger "Thinking..." in UI
                try:
                    await context.send_activity(Activity(type=ActivityTypes.typing))
                except Exception as e:
                    logger.warning(f"Failed to send typing indicator: {e}")
                
                # 🔹 Step 2: Send the actual text update
                logger.info(f"Dispatching via context.send_activity: {msg}")
                await context.send_activity(msg)
            else:
                logger.warning(f"context has no send_activity. Falling back to default stream.")
                await stream(msg)
        except Exception as e:
            logger.error(f"Failed to stream message '{msg}': {e}")
            await stream(msg)

    # Detect simple closures or markings of satisfaction to avoid the "proceeding" box
    closures = ["okay", "ok", "satisfactory", "thanks", "thank you", "got it", "good", "fine", "done"]
    q_norm = user_query.lower().strip().rstrip('.')

    # Detect queries about past incidents (v3.5 memory + v3.8 DB backup)
    memory_keywords = ["what happened earlier", "previous incident", "last error", "what was the issue", "history", "what did i say"]
    if any(k in q_norm for k in memory_keywords):
        last_sum = _get_state("last_recovery_summary")
        db_history = ""
        
        # v3.8: Also fetch from DB if available
        if user_id != "anonymous":
            try:
                hist = fetch_user_history(user_id, limit=3)
            except Exception as e:
                logger.error(f"DB read failed (history): {e}")
                hist = []
            if hist:
                db_history = "\n\n**Recent Activity (from DB):**\n" + "\n".join(
                    f"- {h['created_at'].strftime('%H:%M')}: {h['message_content'][:100]}..."
                    for h in hist if h['message_type'] == 'user'
                )

        if last_sum or db_history:
            resp = (
                f"Earlier, I investigated an issue. Here is the summary I saved:\n\n"
                f"**Past Incident Summary:**\n"
                f"> {last_sum if last_sum else 'No detailed summary found in local memory.'}\n"
                f"{db_history}\n\n"
                f"Is there anything else you'd like to check?"
            )
            aistudio_conv_state.add_conv_input_as_param("response", resp)
            await ai_studio_stream(resp)
            return {
                "intent": "natural",
                "state": "done",
                "response": resp,
                "workflow_selected": None,
                "collected_params": {},
            }

    # 🔹 Immediate acknowledgement for better UX
    if q_norm not in closures:
        await ai_studio_stream("I’m proceeding with your query… Please wait a moment.")
        await asyncio.sleep(0.6)

    if q_norm in closures:
        # Reset state and acknowledge briefly
        aistudio_conv_state.add_dialog_input_as_param(dialog_name, "state", "done")
        aistudio_conv_state.add_conv_input_as_param("state", "done")
        aistudio_conv_state.add_conv_input_as_param("response", "You're welcome! Let me know if you need anything else.")
        await ai_studio_stream("You're welcome! Let me know if you need anything else.")
        
        # 🔹 v3.9: CRITICAL — Save the cleared state before returning
        _save_state("workflow_selected", None)
        _save_state("current_intent", "natural")
        _save_state("collected_params", "{}")
        _save_state("state", "done")
        
        return {
            "intent": "natural",
            "state": "done",
            "response": "You're welcome! Let me know if you need anything else.",
            "workflow_selected": None,
            "collected_params": {},
        }

    user_confirmation = None
    if previous_state in ("need_confirm", "need_param_confirm") and workflow_selected:

        # ── During need_param_confirm: check if user is correcting a value ──
        # e.g. "deduction is 2000" or "change name to John" before saying Yes
        if previous_state == "need_param_confirm":
            _all_param_names = list(collected_params.keys())
            if _all_param_names:
                _corrections = remediation_agent_extract_params(user_query, _all_param_names)
                _valid_corrections = {
                    k: v for k, v in _corrections.items()
                    if v and v.lower() not in ("null", "none", "n/a", "na", "unknown")
                }
                if _valid_corrections:
                    # User is updating a value — apply and re-show confirmation (don't execute yet)
                    collected_params.update(_valid_corrections)
                    logger.info(f"ParamConfirm: User corrected params: {_valid_corrections}")
                    # Leave user_confirmation = None so the flow re-shows the updated confirmation
                else:
                    # No corrections found — treat as Yes/No confirmation
                    user_confirmation = user_query
            else:
                user_confirmation = user_query
        else:
            user_confirmation = user_query

    
    # Handle multi-turn parameter extraction
    if previous_state == "need_params" and workflow_selected:

        # ── Cancel detection via LLM ─────────────────────────────────────────
        # Use the LLM to detect any natural language cancellation intent
        # (covers: "cancel", "stop", "forget it", "I changed my mind", "don't do this", etc.)
        _cancel_check_prompt = f"""Does this message express a desire to CANCEL, STOP, or ABORT the current task?
Answer with a single word: YES or NO.

Message: "{user_query}"
"""
        try:
            _cancel_intent = vertex_generate(_cancel_check_prompt).strip().upper()
        except Exception:
            _cancel_intent = "NO"

        if _cancel_intent.startswith("YES"):
            logger.info(f"MultiTurn: LLM detected cancel intent in: '{user_query[:60]}'")
            _cancel_msg = "No problem! I've cancelled the process. Let me know if there's anything else I can help you with."
            # Clear state immediately
            aistudio_conv_state.add_conv_input_as_param("state", "done")
            aistudio_conv_state.add_conv_input_as_param("workflow_selected", None)
            aistudio_conv_state.add_conv_input_as_param("collected_params", "{}")
            aistudio_conv_state.add_conv_input_as_param("current_intent", None)
            aistudio_conv_state.add_conv_input_as_param("response", _cancel_msg)
            await ai_studio_stream(_cancel_msg)
            return {
                "intent": "natural",
                "state": "done",
                "response": _cancel_msg,
                "workflow_selected": None,
                "collected_params": {},
            }

        # ── Param extraction ───────────────────────────────────────────────
        # Determine which parameters are still missing
        _, missing = remediation_agent_check_params(workflow_selected, collected_params)
        missing_names = [p["name"] for p in missing]

        if missing_names:
            # Extract from the new user query
            extracted = remediation_agent_extract_params(user_query, missing_names)
            for k, v in extracted.items():
                if v and v.lower() not in ("null", "n/a", "na", "none", "unknown"):
                    collected_params[k] = v
                elif v and v.lower() in ("n/a", "na", "none", "unknown"):
                    # User explicitly said they don't have it — skip
                    collected_params[k] = "N/A"
                    logger.info(f"MultiTurn: Param '{k}' marked N/A by user.")

            # ── LLM: detect if user is saying they don't have the information ──
            # Handles: "not with me", "I don't know", "I don't have the emp id", etc.
            _no_info_prompt = f"""Does this message indicate the user does NOT have or CANNOT provide the requested information?
Answer with a single word: YES or NO.

Message: "{user_query}"
"""
            try:
                _no_info_intent = vertex_generate(_no_info_prompt).strip().upper()
            except Exception:
                _no_info_intent = "NO"

            if _no_info_intent.startswith("YES"):
                # Mark all still-missing params as N/A so the flow continues without infinite looping
                for name in missing_names:
                    if name not in collected_params:
                        collected_params[name] = "N/A"
                        logger.info(f"MultiTurn: Param '{name}' auto-marked N/A (LLM: user said unavailable).")

            current_intent = current_intent or "remediation"


    # Build basic history from previous turns for conversational feel
    history = aistudio_conv_state.get_conv_input_as_param("history") or ""
    # Trim history if too long
    if len(history) > 2000:
        history = history[-2000:]

    try:
        output = await _run_agentic_flow_internal(
            user_query=user_query,
            collected_params=collected_params,
            user_confirmation=user_confirmation,
            workflow_selected=workflow_selected,
            current_intent=current_intent,
            previous_state=previous_state,
            session_token=session_token,
            tried_ids=tried_ids,
            stream=ai_studio_stream,
            kb_project_id=kb_project_id,
            kb_secret=kb_secret,
            history=history,
            last_recovery_summary=_get_state("last_recovery_summary")
        )
    except Exception as e:
        logger.error(f"Error in agentic flow: {e}", exc_info=True)
        output = {
            "intent":            current_intent or "error",
            "state":             "error",
            "response":          "Something went wrong on my end. Please try again or contact your administrator.",
            "workflow_selected": workflow_selected,
            "collected_params":  collected_params,
            "tried_ids":         tried_ids,
        }

    def _save_state(key, val):
        aistudio_conv_state.add_dialog_input_as_param(dialog_name, key, val)
        aistudio_conv_state.add_conv_input_as_param(key, val)

    _save_state("collected_params",   json.dumps(output["collected_params"]))
    _save_state("workflow_selected",  json.dumps(output["workflow_selected"]))
    _save_state("current_intent",     output["intent"])
    _save_state("tried_workflow_ids", json.dumps(output.get("tried_ids", [])))
    _save_state("state",              output["state"])
    
    # Update history for next turn
    new_history = f"{history}\nUser: {user_query}\nAssistant: {output['response']}"
    _save_state("history", new_history)
    
    aistudio_conv_state.add_conv_input_as_param("response", output["response"])

    # v3.8: Save assistant response (FINAL thing)
    try:
        save_message(
            session_id=session_id,
            sender="AE-Bot",
            message_type="assistant",
            message_content=output["response"],
            metadata={
                "intent": output.get("intent"),
                "final_status": output.get("state"),
                "user_id": user_id,
            }
        )
    except Exception as e:
        logger.error(f"DB write failed (assistant message): {e}")

    return output


# ===========================================================================
# ─── INTERACTIVE CONVERSATION LOOP (CLI) ─────────────────────────────────────
# ===========================================================================

async def run_conversation_loop(session_token: Optional[str] = None) -> None:
    print("\n" + "=" * 60)
    print("  RPA Agentic Assistant v3  (Vertex AI + T4 Live + KB)")
    print("=" * 60)
    print("Type your query. Type 'exit' to quit.\n")

    collected_params:    Dict[str, str] = {}
    workflow_selected:   Optional[Dict] = None
    current_intent:      Optional[str]  = None
    awaiting_confirm:    bool           = False
    awaiting_params:     bool           = False
    pending_param_names: List[str]      = []
    tried_ids:           List[str]      = []

    async def cli_stream(msg: str) -> None:
        # In CLI mode, print each user-facing streamed message as it arrives
        print(f"\n  ⟶ {msg}\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("exit", "quit", "q"):
            print("Assistant: Goodbye!")
            break
        if not user_input:
            continue

        if awaiting_params and workflow_selected and pending_param_names:
            extractor = diagnostic_agent_extract_params if current_intent == "diagnostic" else remediation_agent_extract_params
            extracted = extractor(user_input, pending_param_names)
            for k, v in extracted.items():
                if v is not None:
                    collected_params[k] = v

            still_missing = [n for n in pending_param_names if not collected_params.get(n)]
            if still_missing:
                print(f"\nAssistant: I still need: {still_missing}. Could you provide those?\n")
                pending_param_names = still_missing
                continue

            awaiting_params     = False
            pending_param_names = []

        if awaiting_confirm and workflow_selected:
            exec_result = await remediation_agent_execute(
                workflow=workflow_selected,
                collected_params=collected_params,
                user_confirmation=user_input,
                session_token=session_token,
                download_output=True,
                output_save_path="./output.xlsx",
            )
            print(f"\nAssistant: {exec_result['message']}\n")
            if exec_result.get("downloaded_file"):
                print(f"Assistant: Output saved to: {exec_result['downloaded_file']}\n")
            collected_params  = {}
            workflow_selected = None
            current_intent    = None
            awaiting_confirm  = False
            continue

        result = await _run_agentic_flow_internal(
            user_query=user_input,
            collected_params=collected_params,
            workflow_selected=workflow_selected,
            current_intent=current_intent,
            session_token=session_token,
            tried_ids=tried_ids,
            stream=cli_stream,
        )

        # For recovery intent, stream_messages were already printed via cli_stream
        # For others, print the final response
        if result.get("intent") != "recovery":
            print(f"\nAssistant: {result['response']}\n")

        current_intent    = result["intent"]
        workflow_selected = result.get("workflow_selected")
        tried_ids         = result.get("tried_ids", [])

        if result["state"] == "need_params":
            awaiting_params     = True
            pending_param_names = [p["name"] for p in (result.get("missing_params") or [])]
        elif result["state"] == "need_confirm":
            awaiting_confirm = True
        elif result["state"] == "done":
            collected_params    = {}
            workflow_selected   = None
            current_intent      = None
            awaiting_confirm    = False
            awaiting_params     = False
            pending_param_names = []
            tried_ids           = []
            if result.get("downloaded_file"):
                print(f"Assistant: Output saved to: {result['downloaded_file']}\n")


# ===========================================================================
# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
# ===========================================================================

if __name__ == "__main__":
    token = t4_authenticate()
    agents = t4_check_agent_status()
    if agents:
        print(f"T4 Agent: {agents[0].get('agentState', 'UNKNOWN')}")
    asyncio.run(run_conversation_loop(session_token=token))