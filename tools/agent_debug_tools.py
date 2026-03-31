"""
Sync tools for agent debug log extraction and analysis.
Used by the Orchestrator for conversational log diagnostics.
"""

import logging
import io
import zipfile
import time
import re
import json
import os
from datetime import datetime, timedelta
from typing import Any

from config.llm_client import llm_client
from tools.base import get_ae_client
from tools.registry import tool_registry, ToolDefinition

logger = logging.getLogger("ops_agent.tools.agent_debug")


def _safe_json(data: Any) -> str:
    try:
        return json.dumps(data)
    except TypeError:
        return str(data)


# ── Timestamp prefix — a valid AE log line MUST start with this ──
# e.g. 2026-03-06T09:48:21.806+05:30
TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+[+\-]\d{2}:\d{2})"
)

# ── Error keywords — only matched AFTER confirming line has a timestamp ──
# Prevents stack-trace lines (java.net.UnknownHostException etc.)
# from being treated as trigger lines.
ERROR_KEYWORDS = re.compile(
    r"\b(ERROR|FATAL|FAILURE|FAILED|EXCEPTION|PSQLException|"
    r"NullPointerException|StackOverflowError|OutOfMemoryError|"
    r"does not exist|relation .* does not exist|"
    r"An error occurred|UnknownHostException|"
    r"Workflow detected one or more steps with errors|"
    r"Workflow is killing)\b",
    re.IGNORECASE,
)

# ── Thread name extractor: [ThreadName] LEVEL ... ──
THREAD_RE = re.compile(r"\[([^\]]+)\]\s+(?:ERROR|FATAL|WARN)", re.IGNORECASE)

# ── AI token budget ──
_AI_BLOCK_CHAR_LIMIT  = 800     # max chars per representative block
_AI_TOTAL_CHAR_LIMIT  = 12_000  # ~12k chars ≈ ~3k tokens — safe LLM budget
_AI_MAX_TOKENS        = 600     # allow detailed structured response


def _is_trigger_line(line: str) -> bool:
    """
    A valid error trigger line MUST:
    1. Start with an ISO-8601 timestamp  (rules out stack trace lines)
    2. Contain an error keyword

    Prevents java.net.UnknownHostException stack lines from being picked
    up as triggers instead of the actual ERROR log line above them.
    """
    return bool(TIMESTAMP_RE.match(line)) and bool(ERROR_KEYWORDS.search(line))


def _extract_errors_from_file_lines(
    lines: list[str],
    max_errors: int = 10,
    context_lines: int = 50,
    tail_fallback: int = 100,
) -> dict:
    """
    Per-file error extraction using BACKWARD scan.

    Rules:
    - Only timestamped log lines containing error keywords are triggers.
      Stack-trace lines (java.net.*, at com.*, ...) are NEVER triggers.
    - Scan BACKWARD from last line — fast for large files with recent errors.
    - Each trigger gets its OWN independent chunk of up to `context_lines`
      following lines (full stack trace). No merging.
    - Cap at `max_errors` MOST RECENT blocks per file.
    - If NO errors found → return last `tail_fallback` lines as clean preview.
    - All returned blocks sorted chronologically (oldest first).
    """
    n = len(lines)
    if n == 0:
        return {"error_blocks": [], "tail_lines": [], "had_errors": False}

    # ── Step 1: Scan BACKWARD — only timestamped ERROR lines are triggers ──
    trigger_indices = []
    for i in range(n - 1, -1, -1):
        if _is_trigger_line(lines[i]):
            trigger_indices.append(i)

    if not trigger_indices:
        tail = lines[-tail_fallback:] if n > tail_fallback else lines
        return {"error_blocks": [], "tail_lines": tail, "had_errors": False}

    # Restore ascending order (oldest trigger first)
    trigger_indices.reverse()

    # ── Step 2: Cap to max_errors MOST RECENT triggers ──
    if len(trigger_indices) > max_errors:
        trigger_indices = trigger_indices[-max_errors:]

    # ── Step 3: Build one independent chunk per trigger ──
    blocks = []
    for i in trigger_indices:
        line = lines[i]
        end = min(i + context_lines + 1, n)
        chunk = lines[i:end]

        ts_match = TIMESTAMP_RE.match(line)
        timestamp = ts_match.group(1) if ts_match else ""

        thread_match = THREAD_RE.search(line)
        thread = thread_match.group(1).strip() if thread_match else ""

        # Short human-readable message: text after first " - " on trigger line
        msg_match = re.search(r"\s-\s(.+)$", line)
        error_message = msg_match.group(1).strip() if msg_match else line.strip()

        blocks.append({
            "timestamp":     timestamp,
            "thread":        thread,
            "error_message": error_message,   # clean one-liner for summaries
            "trigger_line":  line,
            "line_number":   i + 1,
            "lines":         chunk,           # full 50-line context
        })

    # ── Step 4: Sort chronologically ──
    blocks.sort(key=lambda b: b["timestamp"])

    # ── Step 5: Label after sorting ──
    total = len(blocks)
    for idx, block in enumerate(blocks, start=1):
        block["error_index"] = idx
        block["error_label"] = f"Error {idx} of {total}"

    return {"error_blocks": blocks, "tail_lines": [], "had_errors": True}


def _group_errors(all_error_blocks: list[dict]) -> list[dict]:
    """
    Group similar error blocks by their normalized error_message signature.

    Algorithm:
    - Normalize each error_message by stripping digits, IPs, hostnames,
      and timestamps → produces a stable "signature" string.
    - Group all blocks sharing the same signature together.
    - For each group, pick ONE representative block (the one with the most
      context lines — usually most complete stack trace).
    - Attach group metadata: occurrence_count, first_seen, last_seen,
      affected_threads (unique list).

    Returns a list of representative blocks enriched with group metadata,
    sorted by first_seen timestamp (chronological).
    """
    groups: dict[str, dict] = {}   # signature → group dict

    for block in all_error_blocks:
        msg = block["error_message"]
        # Normalize: remove digits, IPs, ports, hostnames, UUIDs
        sig = re.sub(r"\d+", "N", msg)
        sig = re.sub(r"https?://\S+", "<URL>", sig)
        sig = re.sub(r"\b[a-f0-9\-]{8,}\b", "<ID>", sig, flags=re.IGNORECASE)
        sig = sig[:120].strip()

        if sig not in groups:
            groups[sig] = {
                "signature":        sig,
                "representative":   block,       # will be updated to longest chunk
                "occurrence_count": 0,
                "first_seen":       block["timestamp"],
                "last_seen":        block["timestamp"],
                "affected_threads": set(),
            }

        g = groups[sig]
        g["occurrence_count"] += 1

        # Update time range
        if block["timestamp"] and block["timestamp"] < g["first_seen"]:
            g["first_seen"] = block["timestamp"]
        if block["timestamp"] and block["timestamp"] > g["last_seen"]:
            g["last_seen"] = block["timestamp"]

        # Track unique threads
        if block["thread"]:
            g["affected_threads"].add(block["thread"])

        # Prefer the block with the most context lines as representative
        if len(block["lines"]) > len(g["representative"]["lines"]):
            g["representative"] = block

    # Finalise: convert thread sets to sorted lists, sort groups by first_seen
    result = []
    for g in groups.values():
        g["affected_threads"] = sorted(g["affected_threads"])
        result.append(g)

    result.sort(key=lambda g: g["first_seen"])
    return result


def _build_ai_prompt(all_error_blocks: list[dict]) -> str:
    """
    Build a structured, token-safe prompt for the LLM diagnostic.

    Strategy:
    1. Call _group_errors() to deduplicate — LLM sees unique error TYPES,
       not 10 copies of the same UnknownHostException.
    2. For each group, include:
         - Occurrence count, first/last seen, affected threads
         - Representative trigger line + first 10 context lines (enough for
           stack trace root cause)
    3. Cap per-group at _AI_BLOCK_CHAR_LIMIT chars.
    4. Cap total prompt at _AI_TOTAL_CHAR_LIMIT chars (~12k ≈ ~3k tokens).
    5. Prompt asks for: pattern summary, root cause, bulleted recommendations.
    """
    groups = _group_errors(all_error_blocks)

    sections: list[str] = []
    total_chars = 0

    for g in groups:
        rep = g["representative"]

        # Representative snippet: trigger + first 10 context lines
        snippet_lines = rep["lines"][:11]
        snippet = "\n".join(snippet_lines)
        if len(snippet) > _AI_BLOCK_CHAR_LIMIT:
            snippet = snippet[:_AI_BLOCK_CHAR_LIMIT] + "\n...(truncated)"

        threads_str = ", ".join(g["affected_threads"]) if g["affected_threads"] else "unknown"

        entry = (
            f"[Group: {g['signature'][:80]}]\n"
            f"Occurrences: {g['occurrence_count']} | "
            f"First: {g['first_seen']} | Last: {g['last_seen']}\n"
            f"Threads: {threads_str}\n"
            f"Representative log:\n{snippet}"
        )

        if total_chars + len(entry) > _AI_TOTAL_CHAR_LIMIT:
            sections.append("...(additional error groups omitted due to size limit)")
            break

        sections.append(entry)
        total_chars += len(entry)

    groups_text = "\n\n---\n\n".join(sections)
    n_groups = len(groups)
    n_occurrences = sum(g["occurrence_count"] for g in groups)

    return (
        "You are an expert AutomationEdge Support Engineer analyzing agent diagnostic logs.\n\n"
        f"SUMMARY: {n_occurrences} total error occurrences across {n_groups} unique error type(s).\n\n"
        "For each error group below, identify the pattern and provide actionable guidance.\n\n"
        "Your response MUST follow this structure:\n"
        "**Overall Pattern:** (1-2 sentences describing the dominant issue)\n\n"
        "**Root Cause:** (1-2 sentences — what is actually failing and why)\n\n"
        "**Recommendations:**\n"
        "- (specific actionable fix 1)\n"
        "- (specific actionable fix 2)\n"
        "- (specific actionable fix 3, if applicable)\n\n"
        "**Error Groups:**\n\n"
        f"{groups_text}"
    )


def analyze_agent_logs(
    agent_id: str,
    from_date: str | int = "",
    to_date: str | int = "",
    tail_lines: int = 100,
    **kwargs: Any,
) -> dict:
    """
    Extract and analyze agent logs for a specific period.

    For each dated .log.gz file inside the ZIP:
      - Scan BACKWARD for timestamped ERROR lines only (no false positives
        from stack-trace lines like java.net.UnknownHostException).
      - Capture up to 10 error blocks per file, each with 50 lines of context.
      - If a file has NO errors, return its last 100 lines as a clean tail.

    AI Diagnostic:
      - Groups similar errors by normalized signature across all files.
      - Sends one representative block per unique error type to the LLM.
      - Total prompt capped at ~12k chars; response allowed up to 600 tokens.
      - Diagnostic placed at TOP of report for immediate visibility.

    Results grouped and sorted date/time wise.
    """
    import gzip

    client = get_ae_client()

    # ── 0. Resolve Agent ──
    agents = client.list_agents()
    agent_match = next(
        (
            a for a in agents
            if str(a.get("agentId") or a.get("id")) == agent_id
            or str(a.get("agentName") or a.get("name")).lower() == agent_id.lower()
        ),
        None,
    )

    if not agent_match:
        return {"error": f"Agent '{agent_id}' not found"}

    agent_uuid = agent_match.get("uuid")
    state = (agent_match.get("agentState") or agent_match.get("state") or "UNKNOWN").upper()

    if state not in ("RUNNING", "CONNECTED", "ACTIVE"):
        agent_name = agent_match.get("agentName") or agent_match.get("name") or agent_id
        return {"error": f"Please restart your agent {agent_name} first", "agent_state": state}

    # ── 1. Parse Dates ──
    retention_days = 14
    min_date = datetime.now() - timedelta(days=retention_days)
    warning_retention = False
    warning_span = False

    try:
        if from_date:
            f_dt = (
                datetime.fromisoformat(from_date)
                if isinstance(from_date, str)
                else datetime.fromtimestamp(from_date / 1000)
            )
            if f_dt < min_date:
                f_dt = min_date
                warning_retention = True
        else:
            f_dt = datetime.now() - timedelta(hours=24)

        if to_date:
            t_dt = (
                datetime.fromisoformat(to_date)
                if isinstance(to_date, str)
                else datetime.fromtimestamp(to_date / 1000)
            )
            if t_dt > datetime.now():
                t_dt = datetime.now()
        else:
            t_dt = datetime.now()

        f_ms = int(f_dt.timestamp() * 1000)
        t_ms = int(t_dt.timestamp() * 1000)
        orig_f_dt, orig_t_dt = f_dt, t_dt

        max_span = timedelta(days=5)
        if (t_dt - f_dt) > max_span:
            f_dt = t_dt - max_span
            f_ms = int(f_dt.timestamp() * 1000)
            warning_span = True

    except Exception as e:
        return {"success": False, "error": f"Invalid date format: {e}"}

    # ── 2. Request Logs ──
    logger.info("Requesting logs for agent %s (%s) %s→%s", agent_id, agent_uuid, f_ms, t_ms)
    try:
        req_resp = client.request_agent_debug_logs(agent_uuid, f_ms, t_ms)
        req_id = req_resp.get("id")
    except Exception as e:
        return {"success": False, "error": f"Failed to initiate log extraction: {e}"}

    if not req_id:
        return {"success": False, "error": "Failed to initiate log extraction", "raw": req_resp}

    # ── 3. Poll ──
    logger.info("Polling for log request %s...", req_id)
    log_file_link = None
    zip_bytes_direct = None

    poll_interval_seconds = max(1, int(os.getenv("AE_AGENT_LOG_POLL_INTERVAL_SECONDS", "5")))
    max_wait_seconds = max(poll_interval_seconds, int(os.getenv("AE_AGENT_LOG_MAX_WAIT_SECONDS", "180")))
    max_polls = max(1, (max_wait_seconds + poll_interval_seconds - 1) // poll_interval_seconds)
    last_status = "NEW"

    for _ in range(max_polls):
        time.sleep(poll_interval_seconds)
        try:
            status_resp = client.get_agent_debug_logs(str(req_id))
            specific_status = status_resp.get("status", "") if isinstance(status_resp, dict) else ""
            if (not isinstance(status_resp, dict)) or specific_status not in ("COMPLETE", "COMPLETED", "FAILED", "ERROR"):
                try:
                    list_resp = client.get_agent_debug_logs()
                    if isinstance(list_resp, list):
                        list_match = next(
                            (e for e in list_resp if str(e.get("id")) == str(req_id)),
                            None,
                        )
                        if isinstance(list_match, dict):
                            status_resp = list_match
                except Exception as list_err:
                    logger.warning("Poll list fallback error for %s: %s", req_id, list_err)
            if isinstance(status_resp, dict) and status_resp.get("is_zip"):
                zip_bytes_direct = status_resp.get("log_zip_content")
                break
            if isinstance(status_resp, dict):
                last_status = status_resp.get("status", "") or last_status
            if isinstance(status_resp, dict) and status_resp.get("status") in ("COMPLETE", "COMPLETED"):
                log_file_link = status_resp.get("logFileLink")
                break
            if isinstance(status_resp, dict) and status_resp.get("status") in ("FAILED", "ERROR"):
                return {"success": False, "error": "Log extraction failed on server", "details": status_resp}
        except Exception as e:
            logger.warning("Poll error: %s", e)

    if not log_file_link and not zip_bytes_direct:
        return {
            "success": False,
            "error": "Timed out waiting for logs to be ready",
            "request_id": req_id,
            "agent_id": agent_id,
            "last_status": last_status,
            "waited_seconds": max_wait_seconds,
        }

    # ── 4. Download ZIP ──
    try:
        if zip_bytes_direct:
            zip_bytes = zip_bytes_direct
        else:
            zip_bytes = None
            download_candidates = [
                f"/agent/debuglogs/{req_id}",
                log_file_link,
                f"/agent/debuglogs/download?id={req_id}",
            ]
            last_download_error = ""
            for candidate in download_candidates:
                if not candidate:
                    continue
                try:
                    use_rest_prefix = not str(candidate).startswith("/aeengine/rest/")
                    zip_data = client._authorized_request("GET", candidate, use_rest_prefix=use_rest_prefix)
                    zip_bytes = (
                        zip_data.get("log_zip_content")
                        if isinstance(zip_data, dict) and zip_data.get("is_zip")
                        else zip_data
                    )
                    if isinstance(zip_bytes, (bytes, bytearray)):
                        break
                except Exception as download_err:
                    last_download_error = str(download_err)
                    logger.warning("Download attempt failed via %s: %s", candidate, download_err)

        if not isinstance(zip_bytes, (bytes, bytearray)):
            return {
                "success": False,
                "error": "Failed to download log ZIP (unexpected format)",
                "request_id": req_id,
                "download_attempts": [
                    f"/agent/debuglogs/{req_id}",
                    log_file_link,
                    f"/agent/debuglogs/download?id={req_id}",
                ],
                "raw": str(zip_bytes)[:200] if zip_bytes is not None else last_download_error[:200],
            }

        # ── 5. Process each file in ZIP ──
        date_pattern = re.compile(r"(\d{4}-?\d{2}-?\d{2})")
        results_by_date: dict[str, list[dict]] = {}
        total_files_read  = 0
        total_error_files = 0
        all_error_blocks: list[dict] = []

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            all_names = z.namelist()
            logger.info("ZIP has %d files for agent %s", len(all_names), agent_id)

            def _is_agent_log_member(name: str) -> bool:
                lowered = name.lower()
                base = lowered.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if ".log" in lowered or "stdout" in lowered or "stderr" in lowered:
                    return True
                if base in {"aeagent", "agent"}:
                    return True
                return False

            eligible = [
                n for n in all_names
                if _is_agent_log_member(n)
            ]

            for name in eligible:
                # Date filter: only process files in user's requested range
                basename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                dt_match = date_pattern.search(basename)
                file_date_str = "unknown"
                if dt_match:
                    try:
                        raw = dt_match.group(1).replace("-", "")
                        file_dt = datetime.strptime(raw, "%Y%m%d")
                        file_date_str = file_dt.strftime("%Y-%m-%d")
                        # Allow ±1 day boundary overlap
                        if (
                            file_dt.date() < (orig_f_dt.date() - timedelta(days=1))
                            or file_dt.date() > (orig_t_dt.date() + timedelta(days=1))
                        ):
                            continue
                    except ValueError:
                        pass

                # Read & decompress
                try:
                    with z.open(name) as f:
                        if name.lower().endswith(".gz"):
                            import gzip as _gzip
                            with _gzip.GzipFile(fileobj=f) as gz:
                                text = gz.read().decode("utf-8", errors="ignore")
                        else:
                            text = f.read().decode("utf-8", errors="ignore")
                    file_lines = text.splitlines()
                    total_files_read += 1
                except Exception as ef:
                    logger.warning("Could not read %s: %s", name, ef)
                    continue

                # Extract errors (backward scan, timestamp-gated triggers)
                extraction = _extract_errors_from_file_lines(
                    file_lines,
                    max_errors=10,
                    context_lines=50,
                    tail_fallback=tail_lines,
                )

                file_result = {
                    "filename":         name,
                    "date":             file_date_str,
                    "total_lines":      len(file_lines),
                    "had_errors":       extraction["had_errors"],
                    "error_block_count": len(extraction["error_blocks"]),
                    "error_blocks":     extraction["error_blocks"],
                    "tail_lines":       extraction["tail_lines"],
                }

                if extraction["had_errors"]:
                    total_error_files += 1
                    all_error_blocks.extend(extraction["error_blocks"])

                results_by_date.setdefault(file_date_str, []).append(file_result)

        # ── 6. Group errors & run AI Diagnostic ──
        error_groups: list[dict] = []
        ai_diagnostic = ""
        ai_summary_enabled = str(os.getenv("AE_AGENT_LOG_AI_SUMMARY_ENABLED", "false")).strip().lower() in {
            "1", "true", "yes", "on"
        }

        if all_error_blocks:
            error_groups = _group_errors(all_error_blocks)
            if ai_summary_enabled:
                try:
                    prompt = _build_ai_prompt(all_error_blocks)
                    logger.info(
                        "AI diagnostic prompt: %d chars, %d unique groups",
                        len(prompt), len(error_groups),
                    )
                    ai_diagnostic = llm_client.chat(
                        prompt,
                        system=(
                            "You are an expert AutomationEdge Support Engineer. "
                            "Be structured, concise, and actionable. "
                            "Always follow the exact response format requested."
                        ),
                        max_tokens=_AI_MAX_TOKENS,
                    )
                    logger.info("AI diagnostic complete (%d chars)", len(ai_diagnostic))
                except Exception as llm_err:
                    logger.error("AI diagnostic failed: %s", llm_err)
                    ai_diagnostic = "(AI diagnostic unavailable)"
            else:
                logger.info("Skipping AI diagnostic in analyze_agent_logs because AE_AGENT_LOG_AI_SUMMARY_ENABLED is false")

        # ── 7. Build Report ──
        # Header
        report_lines = [f"### Log Analysis for Agent: {agent_id}"]
        report_lines.append(
            f"**Period:** {f_dt.strftime('%Y-%m-%d %H:%M:%S')} → {t_dt.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        report_lines.append(
            f"**Files Analyzed:** {total_files_read} | "
            f"**Files with Errors:** {total_error_files} | "
            f"**Unique Error Types:** {len(error_groups)}"
        )

        if warning_retention or warning_span:
            report_lines.append("\n> [!IMPORTANT]")
            if warning_retention:
                report_lines.append(
                    f"> Start date adjusted to {f_dt.strftime('%Y-%m-%d %H:%M:%S')} (14-day retention limit)."
                )
            if warning_span:
                report_lines.append(
                    f"> Period capped to 5 days ({f_dt.strftime('%Y-%m-%d')} → {t_dt.strftime('%Y-%m-%d')})."
                )

        # ── AI Diagnostic at TOP for immediate visibility ──
        if ai_diagnostic:
            report_lines.append(f"\n### 🤖 AI Diagnostic & Summary\n{ai_diagnostic}")

        # ── Error group overview ──
        if error_groups:
            report_lines.append("\n### 📊 Error Group Overview")
            for g in error_groups:
                threads = ", ".join(g["affected_threads"]) or "unknown"
                report_lines.append(
                    f"- **{g['signature'][:80]}** — "
                    f"{g['occurrence_count']}x | "
                    f"{g['first_seen'][:19]} → {g['last_seen'][:19]} | "
                    f"Thread(s): `{threads}`"
                )

        # ── Per-file date-wise detail ──
        if not results_by_date:
            report_lines.append(
                "\n> [!WARNING]\n> No log files matched the requested date range."
            )
        else:
            report_lines.append("\n### 📂 File Details (Newest First)")
            for date_str in sorted(results_by_date.keys(), reverse=True):
                report_lines.append(f"\n---\n#### 📅 {date_str}")
                for file_res in results_by_date[date_str]:
                    fname         = file_res["filename"]
                    n_blocks      = file_res["error_block_count"]

                    if not file_res["had_errors"]:
                        tail_preview = file_res["tail_lines"]
                        report_lines.append(
                            f"\n✅ **{fname}** — No errors detected "
                            f"({file_res['total_lines']} lines). "
                            f"Last {len(tail_preview)} lines:"
                        )
                        report_lines.append(
                            f"```log\n{chr(10).join(tail_preview[-10:])}\n```"
                        )
                    else:
                        report_lines.append(
                            f"\n⚠️ **{fname}** — {n_blocks} error block(s) "
                            f"(max 10 per file, 50 lines of context each):"
                        )
                        for block in file_res["error_blocks"]:
                            report_lines.append(
                                f"\n> **{block['error_label']}** | "
                                f"`{block['timestamp']}` | "
                                f"Thread: `{block['thread']}`\n"
                                f"> 💬 _{block['error_message']}_"
                            )
                            preview = block["lines"][:10]
                            report_lines.append(
                                f"```log\n{chr(10).join(preview)}\n"
                                f"... ({len(block['lines'])} lines total)\n```"
                            )

        # ── 8. Suggested Solutions (keyword-based, complements AI) ──
        suggested_solutions = []
        if all_error_blocks:
            all_msgs = " ".join(
                b["error_message"] + " " + b["trigger_line"] for b in all_error_blocks
            ).upper()

            if "UNKNOWNHOSTEXCEPTION" in all_msgs or "NO SUCH HOST" in all_msgs or "NAME IS VALID" in all_msgs:
                suggested_solutions.append(
                    "DNS/Network: Agent cannot resolve the AE server hostname. "
                    "Check DNS settings, VPN status, or the `hosts` file on the agent machine."
                )
            if "CONNECTION RESET" in all_msgs or "UNREACHABLE" in all_msgs:
                suggested_solutions.append(
                    "Network: Agent connection is being reset/dropped. "
                    "Check firewall rules, proxy settings, and AE server health."
                )
            if "INTERRUPTEDEXCEPTION" in all_msgs or "MANUAL_STOP" in all_msgs:
                suggested_solutions.append(
                    "Restart: Agent was stopped/restarted (normal shutdown interrupts). "
                    "Only investigate further if the agent did not come back online."
                )
            if "TIMEOUT" in all_msgs:
                suggested_solutions.append(
                    "Timeout: Increase timeout settings or check server/target application load."
                )
            if "CREDENTIAL" in all_msgs or "AUTHENTICATION" in all_msgs or " 401 " in all_msgs:
                suggested_solutions.append(
                    "Credentials: Update agent credentials or refresh the session in AE console."
                )
            if "OUTOFMEMORY" in all_msgs or "HEAP" in all_msgs:
                suggested_solutions.append(
                    "Memory: Increase Java Heap (-Xmx) in AEAgent.bat / AEAgent.conf."
                )

            if suggested_solutions:
                report_lines.append("\n### 💡 Suggested Solutions")
                for sol in suggested_solutions:
                    report_lines.append(f"- {sol}")

        full_report = "\n".join(report_lines)
        error_found = total_error_files > 0
        per_file_summaries = []
        for date_str in sorted(results_by_date.keys(), reverse=True):
            for file_res in results_by_date[date_str]:
                per_file_summaries.append({
                    "date": date_str,
                    "filename": file_res["filename"],
                    "total_lines": file_res["total_lines"],
                    "had_errors": file_res["had_errors"],
                    "error_block_count": file_res["error_block_count"],
                    "summary": (
                        f"{file_res['error_block_count']} error block(s) detected"
                        if file_res["had_errors"]
                        else f"No errors detected; tail preview captured from {file_res['total_lines']} line(s)"
                    ),
                })

        return {
            "success":          True,
            "agent_id":         agent_id,
            "period":           f"{f_dt.date()} to {t_dt.date()}",
            "files_analyzed":   total_files_read,
            "error_files":      total_error_files,
            "error_found":      error_found,
            "unique_error_types": len(error_groups),
            "error_groups":     [                           # structured group data
                {
                    "signature":       g["signature"],
                    "occurrence_count": g["occurrence_count"],
                    "first_seen":      g["first_seen"],
                    "last_seen":       g["last_seen"],
                    "affected_threads": g["affected_threads"],
                }
                for g in error_groups
            ],
            "results_by_date":  results_by_date,
            "per_file_summaries": per_file_summaries,
            "all_error_blocks": all_error_blocks,
            "report":           full_report,
            "suggested_solutions": suggested_solutions,
            "summary": (
                f"Analyzed {total_files_read} file(s). "
                f"{str(total_error_files) + ' file(s) had errors across ' + str(len(error_groups)) + ' unique error type(s).' if error_found else 'No errors detected.'}"
            ),
        }

    except Exception as e:
        logger.exception("Failed to process log ZIP")
        return {"success": False, "error": f"Failed to process log ZIP: {e}"}


# ── Register tool ──
tool_registry.register(
    ToolDefinition(
        name="analyze_agent_logs",
        description=(
            "Extract and AI-analyze logs from an AutomationEdge AGENT for a specific time period. "
            "Processes each dated .log.gz file in the ZIP independently: scans BACKWARD for "
            "timestamped ERROR lines only (never stack-trace lines), captures up to 10 error blocks "
            "per file with 50 lines of context, sorted date/time wise. Falls back to last 100 lines "
            "for clean files. Groups similar errors by normalized signature across all files, sends "
            "one representative block per unique type to LLM (12k char cap, 600 token response). "
            "AI Diagnostic & Summary placed at top of report. "
            "Use for agent-level issues: connectivity, DNS, credentials, service crashes. "
            "Requires agent_id and time range. Call list_agents first if agent_id unknown. "
            "Dates must be ISO format (YYYY-MM-DDTHH:MM:SS)."
        ),
        category="diagnostics",
        tier="medium_risk",
        parameters={
            "agent_id": {
                "type": "string",
                "description": "REQUIRED: Agent name or ID. Call 'list_agents' first if unknown.",
            },
            "from_date": {
                "type": "string",
                "description": "Start date/time (ISO: YYYY-MM-DDTHH:MM:SS). Defaults to last 24h. Server retains ~14 days.",
            },
            "to_date": {
                "type": "string",
                "description": "End date/time (ISO: YYYY-MM-DDTHH:MM:SS). Defaults to now.",
            },
            "tail_lines": {
                "type": "integer",
                "description": "Lines to return for clean (no-error) files (default 100).",
            },
        },
        required_params=[],
        use_when="User asks for agent logs, why an agent is offline, or provides a date range for bot-runner troubleshooting.",
        avoid_when="User provides a specific Request ID or Execution ID — use 'get_execution_logs' instead.",
    ),
    analyze_agent_logs,
)
