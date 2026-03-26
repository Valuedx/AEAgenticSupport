
import sys
import os
import asyncio
import json
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


async def test_guard_agent_running():
    """Agent is RUNNING → guard returns None → logs should proceed."""
    print("\nTest 1: Agent RUNNING → allow log access")
    from mcp_server.tools.agent_guard import check_agent_for_request

    mock_client = MagicMock()
    mock_client.get_request.return_value = {
        "agentName": "Agent-Prod-01",
        "workflowName": "Invoice_Processing",
    }
    mock_client.list_agents.return_value = [
        {"agentId": "a1", "agentName": "Agent-Prod-01", "agentState": "RUNNING"},
    ]

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result = await check_agent_for_request("12345")
        assert result is None, f"Expected None, got {result}"
        print("✅ PASSED — guard returned None (proceed)")


async def test_guard_agent_connected():
    """Agent is CONNECTED → guard returns None."""
    print("\nTest 2: Agent CONNECTED → allow log access")
    from mcp_server.tools.agent_guard import check_agent_for_request

    mock_client = MagicMock()
    mock_client.get_request.return_value = {
        "agentName": "Agent-Prod-02",
        "workflowName": "Data_Sync",
    }
    mock_client.list_agents.return_value = [
        {"agentId": "a2", "agentName": "Agent-Prod-02", "agentState": "CONNECTED"},
    ]

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result = await check_agent_for_request("12346")
        assert result is None, f"Expected None, got {result}"
        print("✅ PASSED")


async def test_guard_agent_stopped():
    """Agent is STOPPED → guard returns error with restart message."""
    print("\nTest 3: Agent STOPPED → block log access")
    from mcp_server.tools.agent_guard import check_agent_for_request

    mock_client = MagicMock()
    mock_client.get_request.return_value = {
        "agentName": "Agent-Prod-03",
        "workflowName": "Email_Workflow",
    }
    mock_client.list_agents.return_value = [
        {"agentId": "a3", "agentName": "Agent-Prod-03", "agentState": "STOPPED"},
    ]

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result = await check_agent_for_request("12347")
        assert result is not None, "Expected error dict, got None"
        assert result["agent_offline"] is True
        assert "STOPPED" in result["error"]
        assert "Agent-Prod-03" in result["error"]
        assert "restart" in result["error"].lower()
        assert result["workflow_name"] == "Email_Workflow"
        print(f"✅ PASSED — message: {result['error']}")


async def test_guard_agent_unknown():
    """Agent is in UNKNOWN state → guard returns error."""
    print("\nTest 4: Agent UNKNOWN → block log access")
    from mcp_server.tools.agent_guard import check_agent_for_request

    mock_client = MagicMock()
    mock_client.get_request.return_value = {
        "agentName": "Agent-Prod-04",
        "workflowName": "Report_Gen",
    }
    mock_client.list_agents.return_value = [
        {"agentId": "a4", "agentName": "Agent-Prod-04", "agentState": "UNKNOWN"},
    ]

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result = await check_agent_for_request("12348")
        assert result is not None, "Expected error dict, got None"
        assert result["agent_offline"] is True
        assert "UNKNOWN" in result["error"]
        print(f"✅ PASSED — message: {result['error']}")


async def test_guard_agent_disconnected():
    """Agent is DISCONNECTED → guard returns error."""
    print("\nTest 5: Agent DISCONNECTED → block log access")
    from mcp_server.tools.agent_guard import check_agent_for_request

    mock_client = MagicMock()
    mock_client.get_request.return_value = {
        "agentName": "Agent-Prod-05",
        "workflowName": "HR_Onboarding",
    }
    mock_client.list_agents.return_value = [
        {"agentId": "a5", "agentName": "Agent-Prod-05", "agentState": "DISCONNECTED"},
    ]

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result = await check_agent_for_request("12349")
        assert result is not None
        assert result["agent_offline"] is True
        assert "DISCONNECTED" in result["error"]
        print(f"✅ PASSED — message: {result['error']}")


async def test_guard_no_agent_assigned():
    """No agent assigned to request → guard returns None (graceful fallback)."""
    print("\nTest 6: No agent assigned → allow (graceful fallback)")
    from mcp_server.tools.agent_guard import check_agent_for_request

    mock_client = MagicMock()
    mock_client.get_request.return_value = {
        "workflowName": "Scheduled_Task",
        # No agentName field
    }

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result = await check_agent_for_request("99999")
        assert result is None, f"Expected None, got {result}"
        print("✅ PASSED — no agent assigned, guard skipped")


async def test_guard_request_not_found():
    """Request not found → guard returns None (let downstream handle 404)."""
    print("\nTest 7: Request not found → allow (downstream handles 404)")
    from mcp_server.tools.agent_guard import check_agent_for_request

    mock_client = MagicMock()
    mock_client.get_request.side_effect = Exception("Not found")

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result = await check_agent_for_request("00000")
        assert result is None, f"Expected None, got {result}"
        print("✅ PASSED — request not found, guard skipped")


async def test_guard_integrated_request_get_logs():
    """Integration: request_get_logs should return agent_offline error when agent is STOPPED."""
    print("\nTest 8: Integration — request_get_logs with STOPPED agent")
    from mcp_server.tools.request_read import request_get_logs

    mock_client = MagicMock()
    mock_client.get_request.return_value = {
        "agentName": "Agent-Prod-06",
        "workflowName": "Payment_Processing",
    }
    mock_client.list_agents.return_value = [
        {"agentId": "a6", "agentName": "Agent-Prod-06", "agentState": "STOPPED"},
    ]

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result_json = await request_get_logs("55555")
        result = json.loads(result_json)
        assert result.get("agent_offline") is True
        assert "STOPPED" in result.get("error", "")
        assert "Agent-Prod-06" in result.get("message", "")
        print(f"✅ PASSED — logs blocked: {result['error']}")


async def test_guard_integrated_request_get_step_logs():
    """Integration: request_get_step_logs should return error when agent is STOPPED."""
    print("\nTest 9: Integration — request_get_step_logs with STOPPED agent")
    from mcp_server.tools.request_diag import request_get_step_logs

    mock_client = MagicMock()
    mock_client.get_request.return_value = {
        "agentName": "Agent-Prod-07",
        "workflowName": "Approval_Flow",
    }
    mock_client.list_agents.return_value = [
        {"agentId": "a7", "agentName": "Agent-Prod-07", "agentState": "STOPPED"},
    ]

    with patch("mcp_server.tools.agent_guard.get_ae_client", return_value=mock_client):
        result_json = await request_get_step_logs("66666")
        result = json.loads(result_json)
        assert result.get("agent_offline") is True
        print(f"✅ PASSED — step logs blocked: {result['error']}")


async def run_all():
    print("=" * 60)
    print("  Agent Status Guard — Test Suite")
    print("=" * 60)

    tests = [
        test_guard_agent_running,
        test_guard_agent_connected,
        test_guard_agent_stopped,
        test_guard_agent_unknown,
        test_guard_agent_disconnected,
        test_guard_no_agent_assigned,
        test_guard_request_not_found,
        test_guard_integrated_request_get_logs,
        test_guard_integrated_request_get_step_logs,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"❌ FAILED — {test_fn.__name__}: {e}")

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed out of {len(tests)}")
    print(f"{'=' * 60}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all())
