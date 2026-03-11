from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from agents.base_agent import (
    BaseAgent,
    AgentInfo,
    AgentResult,
    AgentCapability,
    AgentStatus,
)
from config.llm_client import llm_client
from rag.engine import get_rag_engine
from state.conversation_state import ConversationState

logger = logging.getLogger("ops_agent.rca")


class RCAAgent(BaseAgent):
    """
    Root Cause Analysis Agent.
    Generates structured RCA reports for business and technical audiences.
    """

    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id="rca_agent",
            name="RCA Specialist",
            description=(
                "Specialist in generating Root Cause Analysis (RCA) reports. "
                "Synthesizes investigation findings, tool logs, and past incidents "
                "into structured reports for business and technical stakeholders."
            ),
            capabilities=[AgentCapability.KNOWLEDGE.value],
            domains=["rpa", "workflow", "ops"],
            status=AgentStatus.ACTIVE,
            priority=60,
        )

    def can_handle(self, user_message: str, context: dict | None = None) -> float:
        msg = user_message.lower()
        if any(w in msg for w in ("rca", "root cause", "analysis report", "what happened")):
            return 0.9
        return 0.2

    def handle(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
        **kwargs,
    ) -> AgentResult:
        state: ConversationState | None = kwargs.get("state")
        tracker = kwargs.get("tracker")
        issue_id = kwargs.get("issue_id", "")

        if not state:
            return AgentResult(response="No conversation state provided.", success=False)

        # Ensure static tool modules are imported so registrations are available.
        import tools  # noqa: F401
        from tools.registry import tool_registry

        tool_result = tool_registry.execute(
            "generate_rca_report",
            conversation_id=state.conversation_id,
            incident_summary=user_message,
            state=state,
            tracker=tracker,
            issue_id=issue_id,
        )
        payload = tool_result.data if isinstance(tool_result.data, dict) else {}
        report = (
            str(payload.get("report") or payload.get("error") or "").strip()
            or tool_result.error
            or "Unable to generate an RCA report right now."
        )
        generated_at = (
            payload.get("generated_at")
            if isinstance(payload, dict)
            else None
        ) or (state.rca_data or {}).get("generated_at")

        return AgentResult(
            response=report,
            success=tool_result.success,
            metadata={"rca_generated_at": generated_at},
        )

    def generate_rca(self, state: ConversationState,
                     incident_summary: str = "",
                     tracker=None, issue_id: str = "") -> str:
        if tracker and issue_id:
            findings = tracker.get_issue_findings(issue_id)
            issue = tracker.issues.get(issue_id)
            affected_wfs = (
                issue.workflows_involved if issue
                else state.affected_workflows
            )
        else:
            findings = state.findings
            affected_wfs = state.affected_workflows

        if not findings:
            return "I need to investigate the issue first before I can generate a credible RCA report. Would you like me to start an investigation?"

        search_query = incident_summary or " ".join(affected_wfs)
        rag = get_rag_engine()
        past_incidents = rag.search_past_incidents(search_query, top_k=3)
        sop_hits = rag.search_sops(search_query, top_k=3)

        def _extract(f):
            if isinstance(f, dict):
                return f
            try:
                return {
                    "category": getattr(f, "category", "general"),
                    "summary": getattr(f, "summary", str(f)),
                    "severity": getattr(f, "severity", "medium"),
                    "details": getattr(f, "details", "")
                }
            except Exception:
                return {"summary": str(f)}

        findings_text = json.dumps(
            [_extract(f) for f in findings],
            indent=2, default=str,
        )

        past_text = ""
        for inc in past_incidents:
            meta = inc.get("metadata", {})
            past_text += (
                f"\n- Past: {meta.get('summary', '')}"
                f"\n  Root Cause: {meta.get('root_cause', '')}"
                f"\n  Resolution: {meta.get('resolution', '')}\n"
            )

        # ── Actionable Next Steps (Feature 4.1) ──
        prevention_steps = []
        for hit in sop_hits[:2]:
            content = str(hit.get("content") or "")
            for line in content.splitlines():
                if any(k in line.lower() for k in ("prevent", "future", "permanent", "recommend")):
                    prevention_steps.append(line.strip(" -*"))

        if state.user_role == "business":
            rca = self._generate_business_rca(
                findings_text, past_text, affected_wfs, incident_summary, prevention_steps
            )
        else:
            rca = self._generate_technical_rca(
                findings_text, past_text, affected_wfs,
                incident_summary, state.tool_call_log, prevention_steps
            )

        state.rca_data = {
            "generated_at": datetime.now().isoformat(),
            "report": rca,
            "user_role": state.user_role,
        }
        self._index_as_past_incident(state, rca)
        return rca

    def _generate_business_rca(self, findings_text, past_text,
                               affected_wfs, summary, prevention_steps=None):
        prevention_text = "\n".join([f"- {s}" for s in (prevention_steps or [])[:3]]) if prevention_steps else "- Follow standard operating procedures.\n- Regular monitoring of workflow health."
        
        prompt = f"""Generate a Root Cause Analysis for a BUSINESS AUDIENCE.
Write in plain English. No technical jargon. Use the STRICT format below.

### [STRICT TEMPLATE]
# Executive Summary
[One paragraph high-level summary]

# What Happened
*   **Incident Summary:** {summary}
*   **Workflows Affected:** {', '.join(affected_wfs)}
[Detailed description of the sequence of events in business terms]

# Business Impact
[Describe delays, affected processes, or customer impact]

# Underlying Cause
[Simplified explanation of why the failure occurred]

# Action Taken
[What was done immediately to resolve the issue]

# Preventive Measures
{prevention_text}
[Any additional recommendations to avoid this in the future]
### [END TEMPLATE]

Investigation Findings:
{findings_text}

Similar Past Incidents:
{past_text}

Keep the total report professional and under 500 words."""

        return llm_client.chat(
            prompt,
            system=(
                "You write clear, non-technical RCA reports for "
                "business stakeholders. You MUST follow the provided markdown template exactly."
            ),
        )

    def _generate_technical_rca(self, findings_text, past_text,
                                affected_wfs, summary, tool_logs, prevention_steps=None):
        tool_log_text = json.dumps(tool_logs[-20:], indent=2, default=str)
        prevention_text = "\n".join([f"- {s}" for s in (prevention_steps or [])[:5]])
        if not prevention_text:
            prevention_text = "- Implement additional error handling guards.\n- Review workflow retry policies."

        prompt = f"""Generate a detailed Technical Root Cause Analysis report.
You MUST follow the STRICT Markdown format below. Be specific with names and IDs.

### [STRICT TEMPLATE]
# RCA Report: {summary}

## 1. Metadata
*   **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
*   **Affected Workflows:** {', '.join(affected_wfs)}
*   **Severity:** [High/Medium/Low based on findings]

## 2. Executive Summary
[Concise technical overview of the incident and resolution]

## 3. Timeline of Events
[List sequential steps from detection to resolution with approximate durations if available]

## 4. Root Cause Chain (The "5 Whys")
[Step-by-step chain: A caused B, which led to C, resulting in failure D]

## 5. Technical Findings
[Deep dive into logs, error codes, and tool outputs]

## 6. Resolution Steps Taken
[Detailed technical steps performed to restore service]

## 7. Corrective & Preventive Actions (CAPA)
{prevention_text}
[Long-term fixes to address the root cause]

## 8. Recommendations
[Additional technical improvements suggested]
### [END TEMPLATE]

Investigation Findings:
{findings_text}

Relevant Tool Call Logs:
{tool_log_text}

Historical Context (Past Incidents):
{past_text}

Instructions:
- Be technical and precise. 
- Use Workflow names, Execution IDs, and specific Error Messages from the logs.
- Ensure the 'Timeline' and 'Root Cause' sections are detailed and logical."""

        return llm_client.chat(
            prompt,
            system="You are an expert RPA Operations Engineer writing a technical RCA. You MUST follow the Markdown template exactly.",
        )

    def _index_as_past_incident(self, state, rca_report):
        try:
            rag = get_rag_engine()
            incident_id = f"INC-AUTO-{state.conversation_id}"
            summary = " ".join(state.affected_workflows) + " - auto-generated"
            root_cause_prompt = (
                f"Extract the root cause in one sentence from this RCA:\n"
                f"{rca_report[:1000]}"
            )
            root_cause = llm_client.chat(root_cause_prompt)
            rag.index_past_incident(
                incident_id=incident_id,
                summary=summary,
                root_cause=root_cause,
                resolution=rca_report[:500],
                workflows_involved=state.affected_workflows,
                category="auto_resolved",
            )
        except Exception as e:
            logger.warning(f"Failed to index past incident: {e}")
