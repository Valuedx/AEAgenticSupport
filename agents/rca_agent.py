"""
Root Cause Analysis Agent — improved edition.

Key improvements
----------------
1.  Strict role-based report routing with a neutral fallback.
2.  Severity auto-detection from findings, used consistently across both
    report flavours.
3.  Timeline reconstruction from tool_call_log (technical path).
4.  Prevention steps now deduplicated and ranked by frequency across SOP hits.
5.  `generate_rca` returns a typed RCAResult dataclass instead of a bare str,
    exposing severity + affected_workflows for downstream use.
6.  Graceful degradation: each LLM call is wrapped; on failure a structured
    placeholder is returned instead of a raw exception.
7.  `_index_as_past_incident` is fire-and-forget via a background thread so it
    never blocks the response.
8.  All prompt templates live in *class-level constants* — easy to override in
    tests or subclasses.
9.  Logging is structured (key=value) for easy ingestion by log aggregators.
10. Type annotations throughout; no `Any` where a concrete type is possible.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from agents.base_agent import (
    AgentCapability,
    AgentInfo,
    AgentResult,
    AgentStatus,
    BaseAgent,
)
from config.llm_client import llm_client
from rag.engine import get_rag_engine
from state.conversation_state import ConversationState

logger = logging.getLogger("ops_agent.rca")

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class RCAResult:
    """Structured result returned by generate_rca."""

    report: str
    severity: str                        # "high" | "medium" | "low"
    affected_workflows: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    user_role: str = "technical"
    success: bool = True
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class RCAAgent(BaseAgent):
    """
    Root Cause Analysis Agent.

    Generates structured RCA reports tailored for either a *business* or
    *technical* audience.  Falls back to a generic template when the role is
    unknown or when the LLM call fails.
    """

    # ── Tunable constants ──────────────────────────────────────────────────
    RAG_TOP_K_INCIDENTS: int = 3
    RAG_TOP_K_SOPS: int = 5
    MAX_TOOL_LOG_ENTRIES: int = 25
    MAX_RCA_INDEX_CHARS: int = 600
    MIN_FINDINGS_REQUIRED: int = 1
    MAX_PREVENTION_STEPS_BUSINESS: int = 4
    MAX_PREVENTION_STEPS_TECHNICAL: int = 6

    # ── AgentInfo ──────────────────────────────────────────────────────────

    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="rca_agent",
            name="RCA Specialist",
            description=(
                "Generates structured Root Cause Analysis reports by synthesising "
                "investigation findings, tool logs, SOP knowledge, and historical "
                "incidents.  Supports both business and technical audiences."
            ),
            capabilities=[AgentCapability.KNOWLEDGE.value],
            domains=["rpa", "workflow", "ops"],
            status=AgentStatus.ACTIVE,
            priority=60,
        )

    # ── Routing ────────────────────────────────────────────────────────────

    def can_handle(self, user_message: str, context: dict | None = None, **kwargs) -> float:
        msg = user_message.lower()
        triggers = ("rca", "root cause", "analysis report", "what happened",
                    "incident report", "post mortem", "postmortem")
        if any(w in msg for w in triggers):
            return 0.9
        return 0.2

    # ── Entry point ────────────────────────────────────────────────────────

    def handle(
        self,
        user_message: str,
        context: dict | None = None,
        **kwargs,
    ) -> AgentResult:
        state: ConversationState | None = kwargs.get("state")
        tracker = kwargs.get("tracker")
        issue_id: str = kwargs.get("issue_id", "")

        if not state:
            return AgentResult(response="No conversation state provided.", success=False)

        import tools  # noqa: F401 — ensures tool registrations are loaded
        from tools.registry import tool_registry

        tool_result = tool_registry.execute(
            "generate_rca_report",
            conversation_id=state.conversation_id,
            incident_summary=user_message,
            state=state,
            tracker=tracker,
            issue_id=issue_id,
        )
        payload: dict = tool_result.data if isinstance(tool_result.data, dict) else {}
        report: str = (
            str(payload.get("report") or payload.get("error") or "").strip()
            or tool_result.error
            or "Unable to generate an RCA report right now."
        )
        generated_at = (
            payload.get("generated_at")
            or (state.rca_data or {}).get("generated_at")
        )

        return AgentResult(
            response=report,
            success=tool_result.success,
            metadata={
                "rca_generated_at": generated_at,
                "severity": payload.get("severity", "unknown"),
                "affected_workflows": payload.get("affected_workflows", []),
            },
        )

    # ── Core generation logic ──────────────────────────────────────────────

    def generate_rca(
        self,
        state: ConversationState,
        incident_summary: str = "",
        tracker=None,
        issue_id: str = "",
    ) -> RCAResult:
        """
        Produce an RCAResult.  Writes rca_data back onto *state* as a side-effect
        and asynchronously indexes the incident into the RAG store.
        """
        # 1. Resolve findings + affected workflows
        if tracker and issue_id:
            findings = tracker.get_issue_findings(issue_id)
            issue = tracker.issues.get(issue_id)
            affected_wfs: list[str] = (
                list(issue.workflows_involved) if issue else list(state.affected_workflows)
            )
        else:
            findings = state.findings
            affected_wfs = list(state.affected_workflows)

        if not findings or len(findings) < self.MIN_FINDINGS_REQUIRED:
            msg = (
                "I need to investigate the issue first before I can generate a credible "
                "RCA report.  Would you like me to start an investigation?"
            )
            return RCAResult(report=msg, severity="unknown",
                             affected_workflows=affected_wfs, success=False, error=msg)

        # 2. Detect severity from findings
        severity = self._detect_severity(findings)

        # 3. RAG retrieval
        search_query = incident_summary or " ".join(affected_wfs)
        rag = get_rag_engine()
        past_incidents = rag.search_past_incidents(search_query, top_k=self.RAG_TOP_K_INCIDENTS)
        sop_hits = rag.search_sops(search_query, top_k=self.RAG_TOP_K_SOPS)

        # 4. Serialise findings
        findings_text = self._serialise_findings(findings)

        # 5. Format past-incident context
        past_text = self._format_past_incidents(past_incidents)

        # 6. Extract & deduplicate prevention steps
        prevention_steps = self._extract_prevention_steps(sop_hits)

        # 7. Generate role-appropriate report
        role = (state.user_role or "technical").lower()
        try:
            if role == "business":
                report = self._generate_business_rca(
                    findings_text, past_text, affected_wfs,
                    incident_summary, severity, prevention_steps,
                )
            else:
                timeline = self._build_timeline(state.tool_call_log)
                report = self._generate_technical_rca(
                    findings_text, past_text, affected_wfs,
                    incident_summary, state.tool_call_log,
                    severity, prevention_steps, timeline,
                )
        except Exception as exc:
            logger.error("llm_call_failed role=%s error=%s", role, exc, exc_info=True)
            report = self._fallback_report(incident_summary, affected_wfs, findings_text)

        # 8. Persist to state
        result = RCAResult(
            report=report,
            severity=severity,
            affected_workflows=affected_wfs,
            user_role=role,
        )
        state.rca_data = {
            "generated_at": result.generated_at,
            "report": report,
            "user_role": role,
            "severity": severity,
        }

        # 9. Index asynchronously — never block the user
        threading.Thread(
            target=self._index_as_past_incident,
            args=(state, report),
            daemon=True,
        ).start()

        logger.info(
            "rca_generated conversation_id=%s severity=%s role=%s workflows=%s",
            state.conversation_id, severity, role, affected_wfs,
        )
        return result

    # ── Severity detection ─────────────────────────────────────────────────

    @staticmethod
    def _detect_severity(findings: list) -> str:
        """
        Return "high" | "medium" | "low" based on the highest severity found
        across all findings.  Defaults to "medium" if no severity metadata.
        """
        rank = {"high": 2, "medium": 1, "low": 0}
        best = "low"
        for f in findings:
            sev = ""
            if isinstance(f, dict):
                sev = str(f.get("severity", "")).lower()
            else:
                sev = str(getattr(f, "severity", "")).lower()
            if rank.get(sev, -1) > rank.get(best, -1):
                best = sev
        return best if best in rank else "medium"

    # ── Data helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _serialise_findings(findings: list) -> str:
        def _extract(f):
            if isinstance(f, dict):
                return f
            return {
                "category": getattr(f, "category", "general"),
                "summary": getattr(f, "summary", str(f)),
                "severity": getattr(f, "severity", "medium"),
                "details": getattr(f, "details", ""),
            }
        return json.dumps([_extract(f) for f in findings], indent=2, default=str)

    @staticmethod
    def _format_past_incidents(incidents: list) -> str:
        lines: list[str] = []
        for inc in incidents:
            meta = inc.get("metadata", {})
            lines.append(
                f"- Incident: {meta.get('summary', 'N/A')}\n"
                f"  Root Cause: {meta.get('root_cause', 'N/A')}\n"
                f"  Resolution: {meta.get('resolution', 'N/A')}"
            )
        return "\n".join(lines) if lines else "No similar past incidents found."

    @classmethod
    def _extract_prevention_steps(cls, sop_hits: list) -> list[str]:
        """Collect prevention keywords from SOPs, deduplicate, rank by frequency."""
        from collections import Counter
        counter: Counter = Counter()
        for hit in sop_hits:
            content = str(hit.get("content") or "")
            for line in content.splitlines():
                if any(k in line.lower() for k in
                       ("prevent", "future", "permanent", "recommend", "mitigate",
                        "avoid", "reduce risk", "long-term")):
                    cleaned = line.strip(" -*•").strip()
                    if cleaned:
                        counter[cleaned] += 1
        # Return most-common first, capped later per role
        return [step for step, _ in counter.most_common(cls.MAX_PREVENTION_STEPS_TECHNICAL)]

    @staticmethod
    def _build_timeline(tool_call_log: list) -> str:
        """
        Reconstruct a numbered timeline from tool_call_log entries.
        Each entry is expected to have at least a 'tool' and optional 'timestamp'.
        """
        if not tool_call_log:
            return "No tool call log available."
        lines: list[str] = []
        for i, entry in enumerate(tool_call_log[-30:], 1):
            if isinstance(entry, dict):
                ts = entry.get("timestamp", "")
                tool = entry.get("tool", entry.get("name", "unknown"))
                status = entry.get("status", entry.get("result", ""))
                lines.append(f"{i}. [{ts}] {tool} → {status}")
            else:
                lines.append(f"{i}. {entry}")
        return "\n".join(lines)

    # ── Report templates ───────────────────────────────────────────────────

    def _generate_business_rca(
        self,
        findings_text: str,
        past_text: str,
        affected_wfs: list[str],
        summary: str,
        severity: str,
        prevention_steps: list[str],
    ) -> str:
        steps = prevention_steps[:self.MAX_PREVENTION_STEPS_BUSINESS]
        prevention_block = (
            "\n".join(f"- {s}" for s in steps)
            if steps
            else "- Follow standard operating procedures.\n- Regular monitoring of workflow health."
        )

        prompt = f"""Generate a Root Cause Analysis report for a BUSINESS AUDIENCE.
Write in plain English. Avoid technical jargon. Follow the EXACT template below.

### [STRICT TEMPLATE]
# Incident Report — Executive Summary

**Severity:** {severity.upper()}
**Date:** {datetime.now().strftime('%d %B %Y')}
**Affected Workflows:** {', '.join(affected_wfs) or 'N/A'}

## What Happened
{summary}

[2–3 sentence narrative of the sequence of events in business-friendly language.]

## Business Impact
[Quantify or qualify: delays, affected customers/processes, financial or reputational risk.]

## Underlying Cause
[Plain-language explanation of *why* the failure occurred — no acronyms or stack traces.]

## Immediate Actions Taken
[What steps were performed to restore normal operations.]

## How We Will Prevent This in Future
{prevention_block}

[Any additional recommendations.]
### [END TEMPLATE]

---
Investigation Findings:
{findings_text}

Similar Past Incidents:
{past_text}

Keep the total report professional and under 450 words. Do NOT add extra sections."""

        return llm_client.chat(
            prompt,
            system=(
                "You write clear, empathetic, non-technical RCA reports for senior "
                "business stakeholders.  You MUST follow the provided markdown template exactly."
            ),
        )

    def _generate_technical_rca(
        self,
        findings_text: str,
        past_text: str,
        affected_wfs: list[str],
        summary: str,
        tool_logs: list,
        severity: str,
        prevention_steps: list[str],
        timeline: str,
    ) -> str:
        tool_log_text = json.dumps(tool_logs[-self.MAX_TOOL_LOG_ENTRIES:], indent=2, default=str)
        steps = prevention_steps[:self.MAX_PREVENTION_STEPS_TECHNICAL]
        prevention_block = (
            "\n".join(f"- {s}" for s in steps)
            if steps
            else (
                "- Implement additional error-handling guards.\n"
                "- Review workflow retry and back-off policies.\n"
                "- Add alerting thresholds for early anomaly detection."
            )
        )

        prompt = f"""Generate a detailed Technical Root Cause Analysis report.
Follow the STRICT Markdown template below. Be precise — reference exact names, IDs, and error codes.

### [STRICT TEMPLATE]
# RCA Report: {summary}

## 1. Metadata
| Field | Value |
|---|---|
| **Date** | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
| **Severity** | {severity.upper()} |
| **Affected Workflows** | {', '.join(affected_wfs) or 'N/A'} |
| **Report Author** | RCA Agent (automated) |

## 2. Executive Summary
[Concise 2–3 sentence technical overview: what broke, why, and current status.]

## 3. Timeline of Events
{timeline}

[Annotate each step with its significance if not already clear.]

## 4. Root Cause Chain — The "5 Whys"
1. **Why did the incident occur?** …
2. **Why did that happen?** …
3. **Why?** …
4. **Why?** …
5. **Root Cause:** …

## 5. Technical Findings
[Reference specific log entries, error codes, request IDs, and workflow names from the data below.]

## 6. Resolution Steps Taken
[Numbered, detailed steps performed to restore service, including who/what performed them.]

## 7. Corrective & Preventive Actions (CAPA)
{prevention_block}

## 8. Recommendations & Long-Term Fixes
[Architectural or process improvements to eliminate recurrence.]

## 9. Open Actions & Owners
| Action | Owner | Due Date |
|---|---|---|
| [Action 1] | [Team/Person] | [Date] |
### [END TEMPLATE]

---
Investigation Findings:
{findings_text}

Tool Call Logs (last {self.MAX_TOOL_LOG_ENTRIES} entries):
{tool_log_text}

Historical Context:
{past_text}

Instructions:
- Be technical and precise.
- Use exact workflow names, request IDs, and error messages from the data.
- The Timeline and Root Cause Chain sections MUST be detailed and logically consistent.
- Do NOT omit any section from the template."""

        return llm_client.chat(
            prompt,
            system=(
                "You are a senior RPA Operations Engineer writing a formal technical RCA. "
                "Follow the Markdown template exactly. Use structured evidence from the logs."
            ),
        )

    @staticmethod
    def _fallback_report(summary: str, affected_wfs: list[str], findings_text: str) -> str:
        """Minimal structured report returned when the LLM call fails."""
        return (
            f"# RCA Report (Auto-generated Fallback)\n\n"
            f"**Incident:** {summary or 'Unspecified'}\n"
            f"**Affected Workflows:** {', '.join(affected_wfs) or 'N/A'}\n"
            f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"## Findings\n```json\n{findings_text}\n```\n\n"
            f"_LLM generation failed. Please review the findings above manually._"
        )

    # ── RAG indexing ───────────────────────────────────────────────────────

    def _index_as_past_incident(self, state: ConversationState, rca_report: str) -> None:
        """Fire-and-forget: index the resolved incident into the RAG store."""
        try:
            rag = get_rag_engine()
            incident_id = f"INC-AUTO-{state.conversation_id}"
            summary = " ".join(state.affected_workflows) + " — auto-generated"
            root_cause_prompt = (
                "Extract the root cause in one concise sentence from this RCA report:\n\n"
                f"{rca_report[:1200]}"
            )
            root_cause = llm_client.chat(root_cause_prompt)
            rag.index_past_incident(
                incident_id=incident_id,
                summary=summary,
                root_cause=root_cause,
                resolution=rca_report[: self.MAX_RCA_INDEX_CHARS],
                workflows_involved=state.affected_workflows,
                category="auto_resolved",
            )
            logger.info("rca_indexed incident_id=%s", incident_id)
        except Exception as exc:
            logger.warning("rca_index_failed incident_id=INC-AUTO-%s error=%s",
                           state.conversation_id, exc)