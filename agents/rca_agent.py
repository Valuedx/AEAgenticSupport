"""
Root Cause Analysis Agent.
Generates structured RCA reports for business and technical audiences.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from config.llm_client import llm_client
from rag.engine import get_rag_engine
from state.conversation_state import ConversationState

logger = logging.getLogger("ops_agent.rca")


class RCAAgent:

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
            return "I need to investigate first before generating an RCA."

        search_query = incident_summary or " ".join(affected_wfs)
        rag = get_rag_engine()
        past_incidents = rag.search_past_incidents(search_query, top_k=3)

        def _extract(f):
            if isinstance(f, dict):
                return f
            return {"category": f.category, "summary": f.summary,
                    "severity": f.severity, "details": f.details}

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
        prevention_text = "\n".join(prevention_steps[:3]) if prevention_steps else "Follow standard operating procedures."
        prompt = f"""Generate a Root Cause Analysis for a BUSINESS AUDIENCE.
Write in plain English. No jargon. Focus on:
1. What happened (business terms)
2. Business impact (delays, affected processes)
3. Why it happened (simplified)
4. What was done to fix it
5. How we prevent recurrence

Incident: {summary}
Affected: {', '.join(affected_wfs)}
Findings:
{findings_text}
Similar Past Incidents:
{past_text}

SOP Recommendations:
{prevention_text}

Format as a clean report with sections. Keep it under 500 words."""

        return llm_client.chat(
            prompt,
            system=(
                "You write clear, non-technical RCA reports for "
                "business stakeholders in insurance."
            ),
        )

    def _generate_technical_rca(self, findings_text, past_text,
                                affected_wfs, summary, tool_logs, prevention_steps=None):
        tool_log_text = json.dumps(tool_logs[-15:], indent=2, default=str)
        prevention_text = "\n".join([f"- {s}" for s in (prevention_steps or [])[:5]])

        prompt = f"""Generate a detailed technical Root Cause Analysis.
Include:
1. Incident Summary
2. Timeline of events (from tool calls and findings)
3. Root Cause Chain (A caused B caused C)
4. Impact Analysis (affected workflows, dependencies, data pipelines)
5. Resolution Steps Taken
6. Corrective Actions / Prevention
7. Recommendations

Incident: {summary}
Affected Workflows: {', '.join(affected_wfs)}
Investigation Findings:
{findings_text}
Tool Call Log:
{tool_log_text}
Similar Past Incidents:
{past_text}

SOP Prevention Guidance:
{prevention_text}

Be specific with workflow names, execution IDs, timestamps, and error details."""

        return llm_client.chat(
            prompt,
            system="You write detailed technical RCA reports for RPA operations teams.",
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
