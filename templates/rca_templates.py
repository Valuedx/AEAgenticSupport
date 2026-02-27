"""
RCA (Root Cause Analysis) report templates.
Provides structured templates for business and technical audiences.
"""

BUSINESS_RCA_TEMPLATE = """
## Root Cause Analysis Report

**Date:** {date}
**Affected Process:** {affected_workflows}
**Prepared For:** Business Stakeholders

---

### What Happened
{incident_summary}

### Business Impact
{business_impact}

### Root Cause
{root_cause}

### Resolution
{resolution}

### Prevention Measures
{prevention}

---
*Report generated automatically by the Operations Support Agent.*
"""

TECHNICAL_RCA_TEMPLATE = """
## Technical Root Cause Analysis

**Incident ID:** {incident_id}
**Date:** {date}
**Severity:** {severity}
**Affected Workflows:** {affected_workflows}

---

### 1. Incident Summary
{incident_summary}

### 2. Timeline
{timeline}

### 3. Root Cause Chain
{root_cause}

### 4. Impact Analysis
{impact}

### 5. Resolution Steps
{resolution}

### 6. Corrective Actions
{corrective_actions}

### 7. Recommendations
{recommendations}

---

### Appendix: Tool Call Log
{tool_log}

*Report generated automatically by the Operations Support Agent.*
"""

ESCALATION_TEMPLATE = """
## Escalation Notice

**Issue:** {issue_summary}
**Severity:** {severity}
**Time:** {timestamp}

### Investigation Summary
{investigation_summary}

### Actions Attempted
{attempts}

### Recommended Next Steps
{recommendation}
"""


def render_business_rca(incident_summary="", business_impact="",
                        root_cause="", resolution="", prevention="",
                        date="", affected_workflows="") -> str:
    return BUSINESS_RCA_TEMPLATE.format(
        date=date,
        affected_workflows=affected_workflows,
        incident_summary=incident_summary,
        business_impact=business_impact,
        root_cause=root_cause,
        resolution=resolution,
        prevention=prevention,
    )


def render_technical_rca(incident_summary="", timeline="",
                         root_cause="", impact="", resolution="",
                         corrective_actions="", recommendations="",
                         incident_id="", date="", severity="",
                         affected_workflows="", tool_log="") -> str:
    return TECHNICAL_RCA_TEMPLATE.format(
        incident_id=incident_id,
        date=date,
        severity=severity,
        affected_workflows=affected_workflows,
        incident_summary=incident_summary,
        timeline=timeline,
        root_cause=root_cause,
        impact=impact,
        resolution=resolution,
        corrective_actions=corrective_actions,
        recommendations=recommendations,
        tool_log=tool_log,
    )


def render_escalation_message(issue_summary="", severity="",
                              attempts="", recommendation="",
                              timestamp="",
                              investigation_summary="") -> str:
    return ESCALATION_TEMPLATE.format(
        issue_summary=issue_summary,
        severity=severity,
        timestamp=timestamp,
        investigation_summary=investigation_summary,
        attempts=attempts,
        recommendation=recommendation,
    )
