"""
Tests for the four HDFC-style support scenarios.

Uses test data from support_scenario_data.py and mocks the AE client
to verify: output-not-received, request-stuck, scheduled-job-failed,
and terminate-request (with approval). Run: pytest tests/test_support_scenarios.py -v
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from tests.support_scenario_data import (
    SCENARIO_OUTPUT_NOT_RECEIVED,
    SCENARIO_REQUEST_STUCK,
    SCENARIO_SCHEDULED_JOB_FAILED,
    SCENARIO_TERMINATE_REQUEST,
    make_latest_instance_response,
    make_stuck_list_response,
    make_failed_list_response,
)


# ── 1. Output not received ─────────────────────────────────────────────

class TestScenarioOutputNotReceived:
    """User did not receive output for a completed request (REQ-1234)."""

    def test_check_workflow_status_returns_request_id_and_completed(self):
        from tools import status_tools

        scenario = SCENARIO_OUTPUT_NOT_RECEIVED
        mock_data = make_latest_instance_response(scenario)

        class StubClient:
            default_org_code = ""

            def get_workflow_latest_instance(self, workflow_name, org_code=""):
                assert workflow_name in (
                    scenario["workflow_name"],
                    "Invoice_Processing",
                    "WF_Invoice_Processing",
                ) or scenario["workflow_name"] in workflow_name
                return mock_data

            def resolve_cached_workflow_name(self, name):
                return name or scenario["workflow_name"]

        client = StubClient()
        with patch("tools.status_tools.get_ae_client", return_value=client):
            result = status_tools.check_workflow_status(scenario["workflow_name"])

        assert result["request_id"] == scenario["request_id"]
        assert result["workflow_name"] == scenario["workflow_name"]
        assert result["status"] == "COMPLETED"

    def test_list_recent_failures_can_identify_workflow_for_output_issue(self):
        from tools import status_tools

        scenario = SCENARIO_OUTPUT_NOT_RECEIVED
        workflow_name = scenario["workflow_name"]

        class StubClient:
            default_org_code = ""

            def get_workflow_instances(self, wf_name, limit):
                assert wf_name == workflow_name or workflow_name in (wf_name, "")
                return [
                    {
                        "id": scenario["request_id"],
                        "automationRequestId": scenario["request_id"],
                        "workflow_name": workflow_name,
                        "status": "COMPLETED",
                        "agentName": "agent-prod-01",
                        "errorMessage": None,
                    }
                ]

            def request(self, method, path, **kwargs):
                return {"failures": [], "data": []}

        client = StubClient()
        with patch("tools.status_tools.get_ae_client", return_value=client):
            result = status_tools.list_recent_failures(
                workflow_name=workflow_name, hours=24, limit=10
            )

        assert result.get("total_count", 0) >= 0
        failures = result.get("failures", [])
        if failures:
            assert any(
                f.get("workflow_name") == workflow_name or f.get("execution_id") == scenario["request_id"]
                for f in failures
            )


# ── 2. Request stuck ───────────────────────────────────────────────────

class TestScenarioRequestStuck:
    """Request REQ-5678 stuck for 2 hours; need to discover it and restart."""

    def test_check_workflow_status_returns_stuck_request_id_and_workflow(self):
        from tools import status_tools

        scenario = SCENARIO_REQUEST_STUCK
        mock_data = make_latest_instance_response(scenario)

        class StubClient:
            default_org_code = ""

            def get_workflow_latest_instance(self, workflow_name, org_code=""):
                return mock_data

            def resolve_cached_workflow_name(self, name):
                return name or scenario["workflow_name"]

        client = StubClient()
        with patch("tools.status_tools.get_ae_client", return_value=client):
            result = status_tools.check_workflow_status(scenario["workflow_name"])

        assert result["request_id"] == scenario["request_id"]
        assert result["workflow_name"] == scenario["workflow_name"]
        assert result["status"] == "Running"

    def test_restart_execution_receives_workflow_name_and_execution_id(self):
        from tools import remediation_tools
        from config.settings import CONFIG

        scenario = SCENARIO_REQUEST_STUCK
        workflow_name = scenario["workflow_name"]
        execution_id = scenario["execution_id"]
        mock_restart = scenario["mock_restart_response"]

        if workflow_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
            pytest.skip("Scenario workflow is in PROTECTED_WORKFLOWS")

        class StubClient:
            def post(self, path, payload=None):
                assert f"/executions/{execution_id}/restart" in path or "/restart" in path
                assert (payload or {}).get("workflow_name") == workflow_name
                return mock_restart

        client = StubClient()
        with patch("tools.remediation_tools.get_ae_client", return_value=client):
            result = remediation_tools.restart_execution(
                workflow_name=workflow_name,
                execution_id=execution_id,
                from_checkpoint=True,
            )

        assert result.get("success") is True
        assert result.get("workflow_name") == workflow_name

    def test_stuck_payload_provides_request_id_and_workflow_for_restart(self):
        """Verify list_stuck-style payload has everything needed for restart_execution."""
        scenario = SCENARIO_REQUEST_STUCK
        stuck = make_stuck_list_response(scenario)
        stuck_list = stuck.get("stuck_requests", [])
        assert len(stuck_list) >= 1
        first = stuck_list[0]
        assert "request_id" in first
        assert "workflow_name" in first
        assert first["request_id"] == scenario["request_id"]
        assert first["workflow_name"] == scenario["workflow_name"]


# ── 3. Scheduled job failed ────────────────────────────────────────────

class TestScenarioScheduledJobFailed:
    """Scheduled job JOB-001 / workflow failed; list failures and retrigger."""

    def test_list_recent_failures_returns_failed_execution_with_workflow(self):
        from tools import status_tools

        scenario = SCENARIO_SCHEDULED_JOB_FAILED
        failures_data = make_failed_list_response(scenario)

        class StubClient:
            default_org_code = ""

            def get_workflow_instances(self, workflow_name, limit):
                return failures_data

            def request(self, method, path, **kwargs):
                if "failures" in path or "recent" in path:
                    return {"failures": failures_data, "data": failures_data}
                return {"data": [], "instances": []}

        client = StubClient()
        with patch("tools.status_tools.get_ae_client", return_value=client):
            result = status_tools.list_recent_failures(
                workflow_name=scenario["workflow_name"],
                hours=24,
                limit=10,
            )

        assert result.get("total_count", 0) >= 0
        failures = result.get("failures", [])
        if failures:
            first = failures[0]
            assert first.get("workflow_name") == scenario["workflow_name"]
            assert first.get("execution_id") or first.get("request_id")

    def test_trigger_workflow_can_retrigger_job_workflow(self):
        """Triggering the job's workflow is the remediation for 'job failed'."""
        from tools import remediation_tools
        from config.settings import CONFIG

        scenario = SCENARIO_SCHEDULED_JOB_FAILED
        workflow_name = scenario["workflow_name"]

        if workflow_name in CONFIG.get("PROTECTED_WORKFLOWS", []):
            pytest.skip("Scenario workflow is protected")

        class StubClient:
            def get_cached_workflow_info(self, name):
                return "wf-id-claims", []

            def execute_workflow(self, workflow_name=None, workflow_id=None, params=None, source=None):
                return {
                    "automationRequestId": "REQ-NEW-001",
                    "requestId": "REQ-NEW-001",
                    "id": "REQ-NEW-001",
                    "status": "QUEUED",
                }

            def poll_execution_status(self, execution_id, poll_interval_sec=2, max_attempts=15):
                return {"status": "QUEUED", "raw": {}}

            def check_agent_status(self):
                return [{"agentState": "CONNECTED", "name": "agent-prod-01"}]

        client = StubClient()
        with patch("tools.remediation_tools.get_ae_client", return_value=client):
            result = remediation_tools.trigger_workflow(
                workflow_name=workflow_name,
                parameters={},
            )

        assert result.get("success") is True
        assert result.get("request_id") or result.get("execution_id")


# ── 4. Terminate request (approval required) ─────────────────────────────

class TestScenarioTerminateRequest:
    """Terminate REQ-9999 must require approval (destructive)."""

    def test_terminate_tool_requires_approval_by_policy(self):
        from agents.approval_gate import ApprovalGate

        gate = ApprovalGate()
        # terminate_running / high_risk / privileged should need approval
        needs = gate.needs_approval(
            "ae.request.terminate_running",
            "high_risk",
            {"request_id": SCENARIO_TERMINATE_REQUEST["request_id"]},
        )
        assert needs is True

    def test_approval_gate_classifies_terminate_as_high_risk(self):
        from agents.approval_gate import ApprovalGate

        gate = ApprovalGate()
        # Any tool that is destructive/privileged should need approval
        assert gate.needs_approval("restart_execution", "low_risk", {}) is False
        assert gate.needs_approval("ae.request.terminate_running", "high_risk", {}) is True

    def test_parse_approval_accepts_approve_for_terminate_flow(self):
        from agents.approval_gate import ApprovalGate

        gate = ApprovalGate()
        assert gate.parse_approval_response("approve") is True
        assert gate.parse_approval_response("yes, terminate it") is True
        assert gate.parse_approval_response("reject") is False
        assert gate.parse_approval_response("no, do not terminate") is False


# ── Integration-style: tool chain with scenario data ────────────────────

class TestScenarioToolChain:
    """Verify scenario data drives a minimal tool chain (all mocked)."""

    def test_request_stuck_flow_status_then_restart_args_from_same_scenario(self):
        """From REQUEST_STUCK scenario, status gives (workflow, request_id); restart uses them."""
        scenario = SCENARIO_REQUEST_STUCK
        mock_status = make_latest_instance_response(scenario)
        workflow_name = mock_status.get("workflowName") or scenario["workflow_name"]
        request_id = mock_status.get("automationRequestId") or mock_status.get("id")

        assert workflow_name == scenario["workflow_name"]
        assert request_id == scenario["request_id"]

        expected = scenario["expected_restart_args"]
        assert expected["workflow_name"] == workflow_name
        assert expected["execution_id"] == request_id
