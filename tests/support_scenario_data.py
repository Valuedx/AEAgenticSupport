"""
Test data for the four HDFC-style support scenarios.

Used by test_support_scenarios.py to drive scenario tests with deterministic
mock AE responses. Scenarios align with:
- OUTPUT_NOT_RECEIVED
- REQUEST_STUCK
- SCHEDULED_JOB_FAILED
- TERMINATE_REQUEST (approval-required)
"""

# ── 1. Output not received ─────────────────────────────────────────────

SCENARIO_OUTPUT_NOT_RECEIVED = {
    "scenario_id": "output_not_received",
    "user_message": "I have not received the output for request REQ-1234",
    "request_id": "REQ-1234",
    "workflow_name": "Invoice_Processing",
    "mock_get_workflow_latest_instance": {
        "status": "COMPLETED",
        "automationRequestId": "REQ-1234",
        "id": "REQ-1234",
        "workflowName": "Invoice_Processing",
        "agentName": "agent-prod-01",
        "errorMessage": None,
        "errorDetails": None,
        # Output was generated but not delivered
        "outputAvailable": True,
        "outputDelivered": False,
    },
    "expected_after_status": {
        "request_id": "REQ-1234",
        "workflow_name": "Invoice_Processing",
        "status": "COMPLETED",
    },
}

# ── 2. Request stuck ────────────────────────────────────────────────────

SCENARIO_REQUEST_STUCK = {
    "scenario_id": "request_stuck",
    "user_message": "Request REQ-5678 has been stuck for 2 hours",
    "request_id": "REQ-5678",
    "execution_id": "REQ-5678",
    "workflow_name": "Report_Generator",
    "mock_get_workflow_latest_instance": {
        "status": "Running",
        "automationRequestId": "REQ-5678",
        "id": "REQ-5678",
        "workflowName": "Report_Generator",
        "agentName": "agent-prod-01",
        "errorMessage": None,
        "createdDate": 1709900000000,  # old timestamp
    },
    "mock_list_stuck_response": {
        "stuck_requests": [
            {
                "request_id": "REQ-5678",
                "workflow_name": "Report_Generator",
                "agent": "agent-prod-01",
                "running_minutes": 125.0,
                "created": "2026-03-08T08:00:00+00:00",
            }
        ],
        "count": 1,
        "threshold_minutes": 60,
    },
    "mock_restart_response": {
        "new_execution_id": "EX-5679",
        "status": "initiated",
        "workflow_name": "Report_Generator",
    },
    "expected_restart_args": {
        "workflow_name": "Report_Generator",
        "execution_id": "REQ-5678",
    },
}

# ── 3. Scheduled job failed ─────────────────────────────────────────────

SCENARIO_SCHEDULED_JOB_FAILED = {
    "scenario_id": "scheduled_job_failed",
    "user_message": "Scheduled job JOB-001 has failed",
    "job_id": "JOB-001",
    "workflow_name": "Claims_Processing_Daily",
    "mock_list_failed_recently": {
        "failures": [
            {
                "request_id": "EX-0042",
                "execution_id": "EX-0042",
                "workflow_name": "Claims_Processing_Daily",
                "agent": "agent-prod-01",
                "error_message": "Connection timeout to database",
                "created": "2026-03-08T02:00:00+00:00",
                "completed": "2026-03-08T02:05:00+00:00",
            }
        ],
        "count": 1,
        "time_range_hours": 24,
    },
    "mock_get_workflow_instances": [
        {
            "id": "EX-0042",
            "automationRequestId": "EX-0042",
            "workflow_name": "Claims_Processing_Daily",
            "status": "Failure",
            "agentName": "agent-prod-01",
            "errorMessage": "Connection timeout to database",
            "completedDate": "2026-03-08T02:05:00Z",
        }
    ],
    "mock_trigger_or_restart_response": {
        "request_id": "REQ-NEW-001",
        "execution_id": "REQ-NEW-001",
        "status": "QUEUED",
    },
    "expected_workflow_for_retrigger": "Claims_Processing_Daily",
}

# ── 4. Terminate request (approval required) ─────────────────────────────

SCENARIO_TERMINATE_REQUEST = {
    "scenario_id": "terminate_request",
    "user_message": "Please terminate request REQ-9999",
    "request_id": "REQ-9999",
    "workflow_name": "Data_Extraction",
    "mock_get_request_status": {
        "request_id": "REQ-9999",
        "status": "Running",
        "workflowName": "Data_Extraction",
        "createdDate": 1709910000000,
        "lastUpdatedDate": 1709913600000,
    },
    "requires_approval": True,
    "approval_tool_name": "ae.request.terminate_running",
    "approval_tier": "high_risk",
    "expected_after_approval": {
        "action": "terminate",
        "request_id": "REQ-9999",
    },
}

# ── Helpers for building mock client responses ───────────────────────────

def make_latest_instance_response(scenario: dict, overrides: dict = None) -> dict:
    """Build get_workflow_latest_instance-style response from scenario."""
    data = dict(scenario.get("mock_get_workflow_latest_instance", {}))
    if overrides:
        data.update(overrides)
    return data


def make_stuck_list_response(scenario: dict) -> dict:
    """Build list_stuck-style response."""
    return dict(scenario.get("mock_list_stuck_response", {"stuck_requests": [], "count": 0}))


def make_failed_list_response(scenario: dict) -> list:
    """Build list_failed_recently / get_workflow_instances-style list."""
    failed = scenario.get("mock_list_failed_recently", {}).get("failures", [])
    if failed:
        return failed
    return scenario.get("mock_get_workflow_instances", [])
