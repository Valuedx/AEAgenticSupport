"""
AE Diagnostic Tools — metadata-first evidence collection and LLM diagnosis.

Provides a log evidence processing pipeline that:
1. Extracts relevant log slices by time window, request ID, or step name
2. Extracts Java exception chains with Caused-by context
3. Collapses repeated log noise
4. Normalizes timestamps across sources
5. Merges multiple log streams chronologically
6. Builds a compact structured evidence pack for LLM diagnosis
7. Runs LLM diagnosis with confidence scoring

All local-processing functions are stateless and operate on log text
already retrieved by the existing AutomationEdgeClient.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence, Union

from tools.base import ToolDefinition, get_ae_client
from tools.registry import tool_registry

logger = logging.getLogger("ops_agent.tools.ae_diagnostic")

# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

_AE_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2})"
)

_TS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_AE_TS_RE, "ISO_OFFSET"),
    (re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"), "%Y-%m-%d %H:%M:%S,%f"),
    (re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"), "%Y-%m-%d %H:%M:%S.%f"),
    (re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)"), "ISO_Z"),
    (re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"), "%Y-%m-%d %H:%M:%S"),
]

_IST_OFFSET_RE = re.compile(
    r"^(?P<dt>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+IST\s+\((?P<offset>[+-]\d{2}:\d{2})\)"
)


def parse_timestamp_from_line(
    line: str, default_tz: timezone = timezone.utc,
) -> Optional[datetime]:
    """Parse the leading timestamp from a log line.

    Handles AE format (2026-03-06T09:48:21.806+05:30), standard ISO-8601,
    space-separated timestamps with comma/dot millis, and IST offset notation.
    """
    m = _IST_OFFSET_RE.match(line)
    if m:
        base = datetime.strptime(m.group("dt"), "%Y-%m-%d %H:%M:%S")
        off = m.group("offset")
        sign = 1 if off[0] == "+" else -1
        hh, mm = off[1:].split(":")
        tz = timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
        return base.replace(tzinfo=tz)

    for pattern, fmt in _TS_PATTERNS:
        m2 = pattern.match(line)
        if not m2:
            continue
        raw = m2.group(1)
        if fmt == "ISO_Z":
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if fmt == "ISO_OFFSET":
            cleaned = raw
            if len(cleaned) >= 6 and cleaned[-3] != ":":
                cleaned = cleaned[:-2] + ":" + cleaned[-2:]
            return datetime.fromisoformat(cleaned)
        dt = datetime.strptime(raw, fmt)
        return dt.replace(tzinfo=default_tz)
    return None


# ---------------------------------------------------------------------------
# ae_extract_log_time_window
# ---------------------------------------------------------------------------

def ae_extract_log_time_window(
    log_text: str,
    start_time: Union[str, datetime],
    end_time: Union[str, datetime],
    *,
    context_before_lines: int = 3,
    context_after_lines: int = 10,
) -> str:
    """Keep only log lines within [start_time, end_time] plus context padding."""
    if isinstance(start_time, str):
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    else:
        start_dt = start_time
    if isinstance(end_time, str):
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    else:
        end_dt = end_time

    lines = log_text.splitlines()
    selected_indices: list[int] = []
    start_utc = start_dt.astimezone(timezone.utc)
    end_utc = end_dt.astimezone(timezone.utc)

    for idx, line in enumerate(lines):
        ts = parse_timestamp_from_line(line)
        if not ts:
            continue
        ts_utc = ts.astimezone(timezone.utc)
        if start_utc <= ts_utc <= end_utc:
            lo = max(0, idx - context_before_lines)
            hi = min(len(lines), idx + context_after_lines + 1)
            selected_indices.extend(range(lo, hi))

    selected_indices = sorted(set(selected_indices))
    return "\n".join(lines[i] for i in selected_indices)


# ---------------------------------------------------------------------------
# ae_extract_log_by_request_id
# ---------------------------------------------------------------------------

def ae_extract_log_by_request_id(
    log_text: str,
    request_id: Union[int, str],
    *,
    context_radius: int = 5,
) -> str:
    """Keep only log lines mentioning the request ID, plus surrounding context."""
    request_id_str = str(request_id)
    lines = log_text.splitlines()
    selected: list[int] = []
    pattern = re.compile(rf"\b{re.escape(request_id_str)}\b")
    for idx, line in enumerate(lines):
        if pattern.search(line):
            selected.extend(
                range(max(0, idx - context_radius), min(len(lines), idx + context_radius + 1))
            )
    selected = sorted(set(selected))
    return "\n".join(lines[i] for i in selected)


# ---------------------------------------------------------------------------
# ae_extract_log_by_step_name
# ---------------------------------------------------------------------------

def ae_extract_log_by_step_name(
    log_text: str,
    step_name: str,
    *,
    context_radius: int = 5,
    case_sensitive: bool = False,
) -> str:
    """Keep only log lines mentioning the step name, plus surrounding context."""
    lines = log_text.splitlines()
    selected: list[int] = []
    needle = step_name if case_sensitive else step_name.lower()
    for idx, line in enumerate(lines):
        hay = line if case_sensitive else line.lower()
        if needle in hay:
            selected.extend(
                range(max(0, idx - context_radius), min(len(lines), idx + context_radius + 1))
            )
    selected = sorted(set(selected))
    return "\n".join(lines[i] for i in selected)


# ---------------------------------------------------------------------------
# ae_extract_exception_chain
# ---------------------------------------------------------------------------

_EXCEPTION_LINE_RE = re.compile(
    r"(?P<etype>[A-Za-z_$][\w.$]+(?:Exception|Error))(?::\s*(?P<msg>.*))?"
)
_STACK_RE = re.compile(r"^\s*at\s+[\w.$_]+\([^)]*\)")
_CAUSED_BY_RE = re.compile(r"^\s*Caused by:\s+([\w.$]+)(?::\s*(.*))?$")


def ae_extract_exception_chain(log_text: str) -> list[dict[str, Any]]:
    """Extract Java exception chains: type, message, Caused-by list, top stack frames."""
    lines = log_text.splitlines()
    chains: list[dict[str, Any]] = []

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = _EXCEPTION_LINE_RE.search(line)
        if not m:
            idx += 1
            continue

        exception_type = m.group("etype")
        message = (m.group("msg") or "").strip()
        caused_by: list[dict[str, str]] = []
        top_frames: list[str] = []

        cursor = idx + 1
        while cursor < len(lines):
            current = lines[cursor]
            caused = _CAUSED_BY_RE.match(current)
            if caused:
                caused_by.append({
                    "exception_type": caused.group(1),
                    "message": (caused.group(2) or "").strip(),
                })
                cursor += 1
                continue
            if _STACK_RE.match(current):
                if len(top_frames) < 8:
                    top_frames.append(current.strip())
                cursor += 1
                continue
            if current.startswith("\t") and current.strip().startswith("at "):
                if len(top_frames) < 8:
                    top_frames.append(current.strip())
                cursor += 1
                continue
            if current.startswith(" ") and not parse_timestamp_from_line(current):
                cursor += 1
                continue
            break

        chains.append({
            "exception_type": exception_type,
            "message": message,
            "caused_by": caused_by,
            "top_frames": top_frames,
        })
        idx = cursor

    return chains


# ---------------------------------------------------------------------------
# ae_collapse_repeated_log_lines
# ---------------------------------------------------------------------------

def ae_collapse_repeated_log_lines(
    log_text: str, *, preserve_first_n: int = 3,
) -> str:
    """Collapse spammy repeated log lines, preserving first N occurrences."""
    lines = log_text.splitlines()
    counts: Counter[str] = Counter(lines)
    seen: Counter[str] = Counter()
    output: list[str] = []

    for line in lines:
        seen[line] += 1
        if counts[line] <= preserve_first_n:
            output.append(line)
            continue
        if seen[line] <= preserve_first_n:
            output.append(line)
            continue
        if seen[line] == preserve_first_n + 1:
            skipped = counts[line] - preserve_first_n
            output.append(f"[... collapsed {skipped} repeated occurrences of: {line[:180]} ...]")

    return "\n".join(output)


# ---------------------------------------------------------------------------
# ae_normalize_log_timestamps
# ---------------------------------------------------------------------------

def ae_normalize_log_timestamps(
    log_text: str,
    *,
    target_tz: timezone = timezone.utc,
) -> list[dict[str, Any]]:
    """Parse every log line into {timestamp (ISO), line} events normalized to target_tz."""
    events: list[dict[str, Any]] = []
    for line in log_text.splitlines():
        ts = parse_timestamp_from_line(line)
        events.append({
            "timestamp": ts.astimezone(target_tz).isoformat() if ts else None,
            "line": line,
        })
    return events


# ---------------------------------------------------------------------------
# ae_merge_log_streams_chronologically
# ---------------------------------------------------------------------------

def ae_merge_log_streams_chronologically(
    sources: dict[str, str],
) -> list[dict[str, Any]]:
    """Merge multiple named log text streams into one chronologically sorted timeline.

    Args:
        sources: mapping of source_name -> log_text (raw string content)
    """
    merged: list[dict[str, Any]] = []

    for source_name, text in sources.items():
        for line in text.splitlines():
            ts = parse_timestamp_from_line(line)
            merged.append({
                "source": source_name,
                "timestamp": ts.astimezone(timezone.utc).isoformat() if ts else None,
                "line": line,
            })

    merged.sort(key=lambda x: (
        x["timestamp"] is None,
        x["timestamp"] or "9999-12-31T23:59:59+00:00",
        x["source"],
    ))
    return merged


# ---------------------------------------------------------------------------
# Enhanced error block extraction (adds step-name + stack-trace following)
# ---------------------------------------------------------------------------

_ERROR_BLOCK_RE = re.compile(
    r"\b(ERROR|SEVERE|FATAL|EXCEPTION|FAILED|TRACEBACK|Caused by:|"
    r"timeout|timed out|connection refused|file not found|unable to|could not)\b",
    re.IGNORECASE,
)

_STEP_NAME_PATTERNS = [
    re.compile(r"failed at\s+([A-Za-z0-9_\- ./]+)", re.IGNORECASE),
    re.compile(r"step\s*[:=]\s*([A-Za-z0-9_\- ./]+)", re.IGNORECASE),
    re.compile(r"workflow step\s*[:=]\s*([A-Za-z0-9_\- ./]+)", re.IGNORECASE),
]


def _extract_step_name_from_lines(lines: Sequence[str]) -> Optional[str]:
    for line in lines:
        for p in _STEP_NAME_PATTERNS:
            m = p.search(line)
            if m:
                return m.group(1).strip()
    return None


def ae_extract_error_blocks(
    log_text: str,
    *,
    context_before: int = 6,
    context_after: int = 18,
    source_name: str = "agent_log",
) -> list[dict[str, Any]]:
    """Extract discrete error blocks with context, following stack traces and Caused-by chains."""
    lines = log_text.splitlines()
    blocks: list[dict[str, Any]] = []
    consumed_until = -1

    for idx, line in enumerate(lines):
        if idx <= consumed_until:
            continue
        if not _ERROR_BLOCK_RE.search(line):
            continue

        start = max(0, idx - context_before)
        end = min(len(lines), idx + context_after + 1)

        cursor = idx + 1
        while cursor < len(lines):
            nxt = lines[cursor]
            if _STACK_RE.match(nxt) or _CAUSED_BY_RE.match(nxt) or (nxt.startswith("\t") and nxt.strip()):
                end = cursor + 1
                cursor += 1
                continue
            if nxt.startswith(" ") and not parse_timestamp_from_line(nxt):
                end = cursor + 1
                cursor += 1
                continue
            break

        block_lines = lines[start:end]
        ts = parse_timestamp_from_line(lines[idx])
        blocks.append({
            "type": "error_block",
            "source": source_name,
            "timestamp": ts.isoformat() if ts else None,
            "step": _extract_step_name_from_lines(block_lines),
            "message": line.strip(),
            "lines": block_lines,
        })
        consumed_until = end - 1

    return blocks


# ---------------------------------------------------------------------------
# ae_build_log_evidence_pack
# ---------------------------------------------------------------------------

def _recommended_next_fetch(
    *,
    failure_step: Optional[str],
    error_message: Optional[str],
    primary_error: Optional[dict[str, Any]],
) -> list[str]:
    """Suggest what evidence to fetch next based on error keywords."""
    text = " ".join(
        filter(None, [
            failure_step or "",
            error_message or "",
            (primary_error or {}).get("message", ""),
        ])
    ).lower()

    recs: list[str] = []
    if any(k in text for k in ["timeout", "timed out", "connection", "refused", "unreachable"]):
        recs.extend(["network route/connectivity context", "dependency endpoint availability", "retry history"])
    if any(k in text for k in ["file", "path", "share", "not found", "access denied"]):
        recs.extend(["input/output path existence", "share mount/access checks", "credential context"])
    if any(k in text for k in ["excel", "browser", "chrome", "driver", "rdp"]):
        recs.extend(["bot machine environment details", "plugin/browser version compatibility", "interactive session state"])
    if any(k in text for k in ["database", "sql", "psql", "connection pool", "deadlock"]):
        recs.extend(["database connectivity", "query/lock analysis", "connection pool health"])
    if not recs:
        recs.extend(["previous workflow step outputs", "recent run history", "machine-level agent logs"])
    return recs


def ae_build_log_evidence_pack(
    *,
    instance_metadata: dict[str, Any],
    step_timeline: Optional[dict[str, Any]],
    merged_stream: Optional[list[dict[str, Any]]] = None,
    raw_log_text: Optional[str] = None,
) -> dict[str, Any]:
    """Build a compact structured evidence pack for LLM diagnosis.

    This is the core function: it takes metadata + logs and produces
    a small, clean payload that an LLM can reason over effectively.
    """
    if merged_stream is None and raw_log_text is None:
        raise ValueError("Provide merged_stream or raw_log_text.")

    if raw_log_text is None:
        raw_log_text = "\n".join(e["line"] for e in merged_stream or [])

    compact_log = ae_collapse_repeated_log_lines(raw_log_text)
    error_blocks = ae_extract_error_blocks(compact_log)
    exception_chains = ae_extract_exception_chain(compact_log)

    primary_error = error_blocks[0] if error_blocks else None
    failure_step = (
        instance_metadata.get("failure_step")
        or (step_timeline or {}).get("failed_step")
    )

    evidence_blocks: list[dict[str, Any]] = []
    for block in error_blocks[:5]:
        serializable = dict(block)
        serializable.pop("lines", None)
        evidence_blocks.append(serializable)

    return {
        "instance_id": instance_metadata.get("instance_id"),
        "workflow_name": instance_metadata.get("workflow_name"),
        "run_status": instance_metadata.get("status"),
        "failure_step": failure_step,
        "error_message": instance_metadata.get("error_message"),
        "timestamps": {
            "start_time": instance_metadata.get("start_time"),
            "end_time": instance_metadata.get("end_time"),
        },
        "machine_context": {
            "bot_machine": instance_metadata.get("bot_machine"),
        },
        "step_timeline": (step_timeline or {}).get("steps") if step_timeline else None,
        "primary_error": {
            "type": primary_error["type"],
            "source": primary_error["source"],
            "timestamp": primary_error["timestamp"],
            "step": primary_error["step"],
            "message": primary_error["message"],
        } if primary_error else None,
        "exception_chains": [
            {k: v for k, v in chain.items() if k != "top_frames" or len(v) <= 5}
            for chain in exception_chains[:5]
        ],
        "evidence_blocks": evidence_blocks,
        "recommended_next_fetch": _recommended_next_fetch(
            failure_step=failure_step,
            error_message=instance_metadata.get("error_message"),
            primary_error=primary_error,
        ),
    }


# ---------------------------------------------------------------------------
# High-level orchestrated tool: build_evidence_pack
# ---------------------------------------------------------------------------

_DEFAULT_EVIDENCE_PACK_TIMEOUT = 45


def _emit_progress(on_progress: Optional[Any], message: str) -> None:
    """Safely call the progress callback if provided."""
    if on_progress is None:
        return
    try:
        if callable(getattr(on_progress, "_emit", None)):
            on_progress._emit(message, force=True)
        elif callable(on_progress):
            on_progress(message)
    except Exception:
        pass


def build_evidence_pack(execution_id: str, **kwargs: Any) -> dict:
    """Fetch metadata, step timeline, and logs for an execution, then build a structured evidence pack.

    This is the primary diagnostic entry point: metadata-first, then targeted log extraction,
    then evidence compaction into a clean payload suitable for LLM diagnosis.

    Keyword args:
        max_wait_seconds: Total wall-clock budget for the entire operation
            (default 45 s).  Log retrieval is the most expensive phase; when
            the budget is close to exhaustion the pipeline gracefully degrades
            to a metadata-only evidence pack and returns a
            ``debug_log_request_id`` so the agent can retry later.
        on_progress: Optional ``ProgressCallback`` or ``Callable[[str], None]``
            for streaming status updates to the user while this long-running
            operation executes.
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    max_wait: int = int(kwargs.get("max_wait_seconds", _DEFAULT_EVIDENCE_PACK_TIMEOUT))
    on_progress = kwargs.get("on_progress")
    deadline = _time.monotonic() + max_wait

    client = get_ae_client()

    # ------------------------------------------------------------------
    # Phase 1: Metadata (fast — single HTTP call)
    # ------------------------------------------------------------------
    _emit_progress(on_progress, "Fetching execution metadata...")
    try:
        instance_metadata = client.get_normalized_instance_metadata(execution_id)
    except Exception as exc:
        logger.error("Failed to fetch instance metadata for %s: %s", execution_id, exc)
        return {"success": False, "error": f"Could not fetch metadata for execution {execution_id}: {exc}"}

    # ------------------------------------------------------------------
    # Phase 2: Step Timeline (fast — single HTTP call)
    # ------------------------------------------------------------------
    step_timeline: Optional[dict[str, Any]] = None
    try:
        step_timeline = client.get_workflow_step_timeline(execution_id)
    except Exception as exc:
        logger.warning("Step timeline unavailable for %s: %s", execution_id, exc)

    # ------------------------------------------------------------------
    # Phase 3: Logs — run inside a thread with remaining-budget timeout
    # ------------------------------------------------------------------
    raw_log_text = ""
    log_pending_meta: Optional[dict[str, Any]] = None

    log_budget = max(1, int(deadline - _time.monotonic()))
    _emit_progress(
        on_progress,
        f"Retrieving execution logs (up to {log_budget}s budget)...",
    )

    def _fetch_logs() -> dict:
        from tools.log_tools import get_execution_logs as _get_logs
        return _get_logs(execution_id, tail=0, timeout_seconds=log_budget)

    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="evidence_logs") as pool:
            future = pool.submit(_fetch_logs)
            log_timeout = max(1, int(deadline - _time.monotonic()))
            try:
                log_result = future.result(timeout=log_timeout)
            except FuturesTimeout:
                future.cancel()
                logger.warning(
                    "Log retrieval for %s exceeded %ds budget — degrading to metadata-only evidence pack",
                    execution_id, max_wait,
                )
                log_pending_meta = {
                    "log_retrieval_status": "timed_out",
                    "timeout_seconds": max_wait,
                }
                log_result = {}

        if log_result.get("log_retrieval_status") == "pending":
            log_pending_meta = {
                "log_retrieval_status": "pending",
                "debug_log_request_id": log_result.get("debug_log_request_id"),
            }
        else:
            log_lines = log_result.get("logs", [])
            if isinstance(log_lines, list):
                raw_log_text = "\n".join(str(l) for l in log_lines)
            elif isinstance(log_lines, str):
                raw_log_text = log_lines
    except Exception as exc:
        logger.warning("Log retrieval failed for %s: %s", execution_id, exc)

    # ------------------------------------------------------------------
    # Graceful degradation when logs are unavailable / still pending
    # ------------------------------------------------------------------
    if not raw_log_text:
        result: dict[str, Any] = {
            "success": True,
            "instance_metadata": instance_metadata,
            "step_timeline": step_timeline,
            "evidence_pack": None,
        }
        if log_pending_meta:
            result["log_retrieval"] = log_pending_meta
            status = log_pending_meta.get("log_retrieval_status", "unavailable")
            req_id = log_pending_meta.get("debug_log_request_id", "")
            if status == "pending" and req_id:
                result["note"] = (
                    f"Logs still being prepared by the server (debug request {req_id}). "
                    "Re-run build_evidence_pack or diagnose_from_evidence_pack in "
                    "~30 seconds to include log evidence."
                )
            else:
                result["note"] = (
                    f"Log retrieval {status} after {max_wait}s. "
                    "Evidence pack built from metadata and step timeline only. "
                    "Re-run with a higher max_wait_seconds or fetch logs separately."
                )
        else:
            result["note"] = "Logs unavailable; evidence pack built from metadata only."
        return result

    # ------------------------------------------------------------------
    # Phase 4: Evidence reduction — time-window filtering
    # ------------------------------------------------------------------
    _emit_progress(on_progress, "Processing and filtering log evidence...")
    end_time = instance_metadata.get("end_time")
    if end_time:
        try:
            if isinstance(end_time, (int, float)):
                end_dt = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc)
            else:
                end_dt = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
            start_dt = end_dt - timedelta(seconds=60)
            windowed = ae_extract_log_time_window(raw_log_text, start_dt, end_dt + timedelta(seconds=10))
            if windowed.strip():
                raw_log_text = windowed
        except Exception as exc:
            logger.debug("Time-window filtering skipped: %s", exc)

    # ------------------------------------------------------------------
    # Phase 5: Build evidence pack
    # ------------------------------------------------------------------
    try:
        evidence_pack = ae_build_log_evidence_pack(
            instance_metadata=instance_metadata,
            step_timeline=step_timeline,
            raw_log_text=raw_log_text,
        )
    except Exception as exc:
        logger.error("Evidence pack build failed: %s", exc)
        return {
            "success": False,
            "error": f"Evidence pack construction failed: {exc}",
            "instance_metadata": instance_metadata,
        }

    _emit_progress(on_progress, "Evidence pack ready.")
    return {
        "success": True,
        "evidence_pack": evidence_pack,
        "instance_metadata": instance_metadata,
        "step_timeline": step_timeline,
    }


# ---------------------------------------------------------------------------
# Standalone exception chain tool
# ---------------------------------------------------------------------------

def extract_exception_chain_tool(log_text: str, **kwargs: Any) -> dict:
    """Extract Java exception chains from log text for targeted diagnosis."""
    if not log_text or not log_text.strip():
        return {"success": False, "error": "No log text provided", "chains": []}

    chains = ae_extract_exception_chain(log_text)
    return {
        "success": True,
        "chain_count": len(chains),
        "chains": chains[:10],
    }


# ---------------------------------------------------------------------------
# LLM Diagnosis from Evidence Pack
# ---------------------------------------------------------------------------

_DIAGNOSIS_SYSTEM_PROMPT = (
    "You are the diagnosis engine for AutomationEdge workflow failures. "
    "You will receive a structured evidence pack, not raw logs. "
    "Your job is to:\n"
    "1. identify the most likely root cause\n"
    "2. assign a confidence score from 0.0 to 1.0\n"
    "3. list up to 3 alternative hypotheses\n"
    "4. explain why the top diagnosis is most likely\n"
    "5. recommend the next evidence fetch if confidence is below 0.8\n"
    "6. suggest only safe remediation actions\n"
    "7. never claim certainty unless the evidence is explicit\n\n"
    "Prioritize: platform metadata > primary error block > exception chain > "
    "prior-step evidence > generic error priors.\n\n"
    "Output ONLY valid JSON with this structure:\n"
    '{"primary_diagnosis": "...", "confidence": 0.0, "alternatives": ["..."], '
    '"reasoning_summary": "...", "recommended_next_fetch": ["..."], '
    '"safe_remediation_candidates": ["..."]}'
)


def diagnose_from_evidence_pack(
    evidence_pack: Optional[dict[str, Any]] = None,
    execution_id: str = "",
    **kwargs: Any,
) -> dict:
    """Run LLM diagnosis on a structured evidence pack.

    If no evidence_pack is provided but execution_id is given,
    builds the evidence pack first.  Accepts the same ``max_wait_seconds``
    and ``on_progress`` kwargs as ``build_evidence_pack``.

    When logs are unavailable (timed out or still pending on the server),
    the function still attempts a metadata-only diagnosis with a capped
    confidence and includes a ``log_retrieval`` field so the agent can
    retry later with full evidence.
    """
    from config.llm_client import llm_client

    log_retrieval_note: Optional[dict[str, Any]] = None

    if evidence_pack is None and execution_id:
        pack_result = build_evidence_pack(execution_id, **kwargs)
        if not pack_result.get("success"):
            return pack_result
        evidence_pack = pack_result.get("evidence_pack")
        log_retrieval_note = pack_result.get("log_retrieval")

        if evidence_pack is None:
            metadata = pack_result.get("instance_metadata") or {}
            step_tl = pack_result.get("step_timeline")
            if metadata:
                evidence_pack = {
                    "instance_id": metadata.get("instance_id"),
                    "workflow_name": metadata.get("workflow_name"),
                    "run_status": metadata.get("status"),
                    "failure_step": (
                        metadata.get("failure_step")
                        or (step_tl or {}).get("failed_step")
                    ),
                    "error_message": metadata.get("error_message"),
                    "timestamps": {
                        "start_time": metadata.get("start_time"),
                        "end_time": metadata.get("end_time"),
                    },
                    "machine_context": {"bot_machine": metadata.get("bot_machine")},
                    "step_timeline": (step_tl or {}).get("steps") if step_tl else None,
                    "primary_error": None,
                    "exception_chains": [],
                    "evidence_blocks": [],
                    "recommended_next_fetch": ["full execution logs (retry after ~30s)"],
                    "_metadata_only": True,
                }
            else:
                return {
                    "success": False,
                    "error": "Could not build evidence pack (logs unavailable)",
                    "instance_metadata": metadata,
                    "log_retrieval": log_retrieval_note,
                }

    if not evidence_pack:
        return {"success": False, "error": "No evidence pack provided and no execution_id given."}

    is_metadata_only = bool(evidence_pack.get("_metadata_only"))
    serializable_pack = json.dumps(evidence_pack, indent=2, default=str)

    metadata_caveat = ""
    if is_metadata_only:
        metadata_caveat = (
            "\n\nIMPORTANT: This evidence pack was built from **metadata only** "
            "because execution logs were not available in time.  Your confidence "
            "MUST NOT exceed 0.5.  Always recommend fetching full execution logs "
            "as the top item in recommended_next_fetch."
        )

    prompt = (
        "Analyze this AutomationEdge workflow failure evidence pack and provide your diagnosis.\n\n"
        f"Evidence Pack:\n```json\n{serializable_pack}\n```"
        f"{metadata_caveat}"
    )

    try:
        raw_response = llm_client.chat(
            prompt,
            system=_DIAGNOSIS_SYSTEM_PROMPT,
            max_tokens=800,
        )

        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            diagnosis = json.loads(cleaned)
        except json.JSONDecodeError:
            diagnosis = {
                "primary_diagnosis": cleaned,
                "confidence": 0.5,
                "alternatives": [],
                "reasoning_summary": "LLM returned non-JSON response; raw text captured as diagnosis.",
                "recommended_next_fetch": [],
                "safe_remediation_candidates": [],
                "raw_response": raw_response,
            }

        if is_metadata_only:
            conf = diagnosis.get("confidence", 1.0)
            if isinstance(conf, (int, float)) and conf > 0.5:
                diagnosis["confidence"] = 0.5
            diagnosis.setdefault("reasoning_summary", "")
            diagnosis["reasoning_summary"] += (
                " [Note: diagnosis based on metadata only — "
                "re-run with full logs for higher confidence.]"
            )

        result: dict[str, Any] = {
            "success": True,
            "diagnosis": diagnosis,
            "evidence_pack_summary": {
                "instance_id": evidence_pack.get("instance_id"),
                "workflow_name": evidence_pack.get("workflow_name"),
                "run_status": evidence_pack.get("run_status"),
                "failure_step": evidence_pack.get("failure_step"),
            },
        }
        if log_retrieval_note:
            result["log_retrieval"] = log_retrieval_note
        if is_metadata_only:
            result["metadata_only"] = True
        return result

    except Exception as exc:
        logger.error("LLM diagnosis failed: %s", exc)
        return {
            "success": False,
            "error": f"Diagnosis LLM call failed: {exc}",
            "evidence_pack_summary": {
                "instance_id": evidence_pack.get("instance_id"),
                "workflow_name": evidence_pack.get("workflow_name"),
            },
        }


# ---------------------------------------------------------------------------
# Tool Registrations
# ---------------------------------------------------------------------------

tool_registry.register(
    ToolDefinition(
        name="build_evidence_pack",
        description=(
            "Build a structured diagnostic evidence pack for a workflow execution. "
            "Fetches instance metadata, step timeline, and logs, then applies "
            "evidence reduction (time-window filtering, noise collapse, error extraction, "
            "exception chain analysis) to produce a compact payload ready for LLM diagnosis. "
            "Use this as the first step when investigating a specific execution failure "
            "before calling diagnose_from_evidence_pack.  "
            "If log retrieval takes longer than max_wait_seconds the tool returns a "
            "metadata-only evidence pack with a log_retrieval.debug_log_request_id "
            "so you can retry later."
        ),
        category="diagnostics",
        tier="read_only",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The numeric execution/request ID to diagnose.",
            },
            "max_wait_seconds": {
                "type": "integer",
                "description": (
                    "Maximum wall-clock seconds to wait for log retrieval "
                    "(default 45). Reduce for faster degraded results."
                ),
            },
        },
        required_params=["execution_id"],
        use_when=(
            "User asks for deep diagnosis of a specific execution failure, "
            "or you need structured evidence before generating an RCA report."
        ),
        avoid_when=(
            "User only wants quick status check — use get_execution_status instead. "
            "User wants agent-level logs across dates — use analyze_agent_logs instead."
        ),
    ),
    build_evidence_pack,
)

tool_registry.register(
    ToolDefinition(
        name="diagnose_from_evidence_pack",
        description=(
            "Run AI-powered diagnosis on a workflow execution to determine "
            "root cause with confidence scoring, alternative hypotheses, and "
            "remediation suggestions. Automatically builds an evidence pack "
            "(metadata + step timeline + filtered logs) for the given execution ID, "
            "then sends it to the LLM for structured diagnosis. Returns JSON "
            "with primary_diagnosis, confidence (0-1), alternatives, reasoning, "
            "and safe_remediation_candidates.  "
            "If logs are unavailable within the time budget, the tool still "
            "returns a metadata-only diagnosis (confidence capped at 0.5) "
            "with a log_retrieval hint for follow-up."
        ),
        category="diagnostics",
        tier="read_only",
        parameters={
            "execution_id": {
                "type": "string",
                "description": "The execution/request ID to diagnose.",
            },
            "max_wait_seconds": {
                "type": "integer",
                "description": (
                    "Maximum wall-clock seconds to wait for log retrieval "
                    "(default 45). Reduce for faster degraded results."
                ),
            },
        },
        required_params=["execution_id"],
        use_when=(
            "User asks 'why did this fail?', 'what is the root cause?', "
            "'diagnose execution X', or you need a structured diagnosis."
        ),
        avoid_when=(
            "User asks for a full RCA report — use generate_rca_report instead. "
            "User asks for raw logs — use get_execution_logs instead."
        ),
    ),
    diagnose_from_evidence_pack,
)

tool_registry.register(
    ToolDefinition(
        name="extract_exception_chain",
        description=(
            "Extract Java exception chains from log text: exception types, messages, "
            "nested Caused-by chains, and top stack frames. Useful for pinpointing "
            "the actual exception hierarchy in AE workflow failures."
        ),
        category="diagnostics",
        tier="read_only",
        parameters={
            "log_text": {
                "type": "string",
                "description": "Raw log text to extract exception chains from.",
            },
        },
        required_params=["log_text"],
        use_when="You have raw log text and need to isolate the exception chain for diagnosis.",
        avoid_when="You don't have log text yet — fetch logs first.",
    ),
    extract_exception_chain_tool,
)
